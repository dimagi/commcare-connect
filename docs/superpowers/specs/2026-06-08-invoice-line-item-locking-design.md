# Invoice line-item locking & post-invoice completed-work changes — Tech Spec

> **Scope — managed opportunities.** The product is standardizing on **managed opps** (an NM approves
> visits; a PM then *agrees*). This design is written for them; non-managed flows are out of scope.

## Summary

A forward-delta invoicing model to eliminate invoice drift and correctly handle post-invoice changes
to completed work.

- Invoice **line items are snapshotted** into a new `CompletedWorkInvoice` table at creation, instead
  of being recomputed on every view (today's drift source).
- Each completed work carries a **watermark** (`invoiced_approved_count`) = how many of its approved
  units have already been billed.
- A new invoice bills only the **delta** — units approved since last billing
  (`saved_approved_count − invoiced_approved_count`).
- **Late approvals** bill on the **next** invoice, never retro-added to the issued one. On an
  already-billed work the delta is always a duplicate (the only way a work's approved count exceeds 1
  — see [Background](#background--how-a-works-approved-count-exceeds-1)).
- **Late rejection is blocked** once a work is billed, so an issued invoice can't be silently lowered.

**Decision:** chosen over the "drop late approvals" alternative (appendix) because product confirmed
late approvals must be paid ([Open question 1](#open-questions-pending-product)).

## Non-goals

- **Correcting historical drift.** This design stops *future* drift; it does **not** reconcile
  invoices that have already drifted. The backfill freezes legacy rows from their current `saved_*`
  values ([§1.8](#18-backfill)), so an already-drifted invoice stays as-is, and the original per-work
  split generally can't be reconstructed. Drift can be *surfaced* read-only
  ([Good to have — legacy drift report](#good-to-have--legacy-drift-report)) but is never
  auto-corrected.

## Current system & why it breaks

`PaymentInvoice.amount` is frozen at creation, but the line items shown are **recomputed on every
view** from the linked `CompletedWork.saved_*` fields — which `CompletedWorkUpdater` recomputes on
every visit approve/reject, with no invoiced-check. This breaks three ways:

1. **Drift** — displayed line items diverge from the frozen `amount`.
2. **Late approval lost** — a post-invoice approval raises the work's count, but the work is excluded
   from future invoices, so the increase is never billed.
3. **Late rejection corrupts** — a post-invoice rejection lowers the count (to 0 under
   `auto_approve_payments`), silently changing an invoice that may be approved/paid.

*What makes an approved visit rejectable:* `review_status != agree` — an approved-but-not-yet-agreed
visit, via the visit-detail Reject button. (An agreed visit is never rejectable — see
[Rejection-guard enforcement](#shared--rejection-guard-enforcement).)

## Background — how a work's approved count exceeds 1

Existing behavior. A work's `saved_approved_count` exceeds 1 **only** via duplicates: a worker
approved more than once for the same `(entity, payment_unit)`. Absent duplicates a work tops out at a
single approved unit, so any late approval that raises an already-billed work's count is **always** a
duplicate. Duplicate approvals are routine, not an edge case, and arise via two paths:

- **Duplicate detection ON** (`OpportunityVerificationFlags.duplicate=True`, default): the repeat
  visit is marked `duplicate` and held from auto-approval, but a reviewer can still **manually
  approve** it.
- **Duplicate detection OFF**: the `duplicate` status resets to `pending`, so the repeat visit flows
  through normal validation and is **auto-approved** under `auto_approve_payments`.

## Definitions

- **Live invoice** = any `InvoiceStatus` except `CANCELLED_BY_NM` and `REJECTED_BY_PM` (these unlink
  their works, rolling them back to editable). `ARCHIVED` counts as live ([§1.6](#16-lifecycle--cancel--reject--pay)).
- **Locked work** = a completed work covered by a live invoice. The lock is **whole-work**: if *any*
  part of the work is billed, *none* of its approved visits are rejectable (incl. a freshly arrived
  duplicate).

## Invariants

Relied on by later sections rather than re-derived:

- **Delta never negative:** `saved_approved_count >= invoiced_approved_count` always — because
  rejection is blocked once any unit is billed ([§1.5](#15-late-approval--rejection-guard)).
- **Watermark = billed-state source of truth:** `invoiced_approved_count` = `SUM(billed_count)` over
  the work's live-invoice rows (stored for fast lookups; kept in sync and guarded in [§1.1](#11-model-changes)).
- **Snapshots immutable:** a `CompletedWorkInvoice` row never changes once created; an invoice's
  `amount` = the sum of its rows and never recomputes.

---

## Approach 1 — Forward-delta (chosen)

Late approvals bill as a delta on the next invoice; line items are snapshotted at creation, so the
current invoice never drifts.

**Minimal example.** Entity Baby-A, a payment unit worth 100:

1. **Jan** — visit approved → Invoice A snapshots one row (`billed_count = 1`, amount 100, Jan);
   watermark → 1; A's `amount` is frozen.
2. **Mar** — a duplicate visit approved (`saved_approved_count → 2`) → Invoice B bills the delta
   `2 − 1 = 1`; watermark → 2. Invoice A is untouched.

### 1.1 Model changes

**New — `CompletedWorkInvoice` (per-work billing record).** One row per `(invoice, completed_work)`
recording how much of that work was billed on that invoice:

```
CompletedWorkInvoice
  invoice         FK → PaymentInvoice  (related_name="work_items", on_delete=PROTECT)
  completed_work  FK → CompletedWork   (on_delete=PROTECT)
  payment_unit    FK → PaymentUnit     (snapshot, for display grouping)
  month           DateField            (period attribution, §1.4)
  billed_count    IntegerField         (delta billed on THIS invoice; > 0)
  amount_local    DecimalField         (frozen)
  amount_usd      DecimalField         (frozen)
  exchange_rate   FK → ExchangeRate    (on_delete=PROTECT; rate for this row's month, §1.3)
  unique (invoice, completed_work)
```

- Each row is **one work (one user, one entity)**.
- Displayed line items = `GROUP BY payment_unit, month` over these rows — today's `get_invoice_items`
  shape, but reading frozen rows instead of recomputing.

**Why a join table, not the existing `CompletedWork.invoice` FK?**

- Forward-delta bills one work across *several* invoices over its life.
- A single FK points to only one invoice and can't store the per-invoice billed amount needed to roll
  back a cancellation; the join table records each `(work, invoice)` contribution.
- So `CompletedWork.invoice` is superseded — dropped after cutover (readers re-pointed in
  [§1.9](#19-releases--expandcontract-rollout) step 3).

**New — `CompletedWork.invoiced_approved_count`** (`IntegerField(default=0)`): the watermark.

- A **count only** — money lives solely in `CompletedWorkInvoice`.
- Maintained on create ([§1.3](#13-creation--snapshot--bump-watermark-atomic)) and cancel/reject
  ([§1.6](#16-lifecycle--cancel--reject--pay)).

**Watermark integrity** — it's a cached copy, kept correct by enforcing the invariant in code at the
call site: the value is written solely by the atomic, `select_for_update`-locked create/rollback code
(§1.3/§1.6), which is the single writer and where correctness comes from.
- **Delete guard:** `invoice on_delete=PROTECT` blocks hard-deleting a `PaymentInvoice` while it has
  rows — the only sanctioned teardown is the §1.6 cancel/reject rollback, which decrements the
  watermark and deletes the rows atomically. PROTECT forces any other delete path (Django admin,
  shell) through that rollback, so the watermark can never be left stale.

### 1.2 Selection — delta-based

Replace `get_uninvoiced_completed_works_qs` with a billable query. The **watermark** decides what is
billable; **date bounds only scope** it:

- **First-billing works** (`invoiced_approved_count == 0`) — scoped by the date window
  (`status_modified_date` is meaningful here).
- **Late deltas** (`invoiced_approved_count > 0`) — **bypass the window**, always bill on the next
  invoice. Their `status_modified_date` is stale (frozen at the original approval,
  [§1.4](#14-when-a-late-approved-visit-gets-billed-and-under-which-month)), so the window can't
  sensibly apply.

```
def get_billable_completed_works_qs(opportunity, start_date=None, end_date=None):
    qs = CompletedWork.objects.filter(
        opportunity_access__opportunity=opportunity,
        status=CompletedWorkStatus.approved,
        saved_approved_count__gt=F("invoiced_approved_count"),
    )
    # Late deltas (already partly billed) bill regardless of the window.
    late_delta = Q(invoiced_approved_count__gt=0)
    in_window = Q()
    if start_date:
        in_window &= Q(status_modified_date__date__gte=start_date)
    if end_date:
        in_window &= Q(status_modified_date__date__lte=end_date)
    return qs.filter(late_delta | in_window)
```

- Billable delta per work = `saved_approved_count − invoiced_approved_count` (≥ 1).
- Brand-new works bill their full count via the same path.

> **Depends on [Open question 2](#open-questions-pending-product):** this query (and the
> [§1.4](#14-when-a-late-approved-visit-gets-billed-and-under-which-month) `invoiced_approved_count ==
> 0` test) use `saved_approved_count`. If managed billing must require PM agreement, the billable
> count changes — resolve before finalizing.

**Why late deltas bypass the date window.** A late duplicate keeps the work at `approved`, so its
`status_modified_date` never moves off the original approval (e.g. January). If that stale date were
windowed it would:

- **(a)** be wrongly excluded from a manual invoice whose `start_date` is after January — silently
  deferring a delta that should bill now;
- **(b)** drag the automated run's lower bound back to January.

Bypassing fixes both: late deltas bill on the next invoice, attributed to the billing month
([§1.4](#14-when-a-late-approved-visit-gets-billed-and-under-which-month)).

**First-billing works still scope by date.**

- `get_start_date_for_invoice` computes `Min(status_modified_date)` over **first-billing** works only
  (not late deltas) — so a stale date no longer drags it.
- **Automated run** sweeps `[that start, previous-month end]`.
- **Manual UI** honors the user's `start_date` as the lower bound.
- A first-billing work older than the bound stays billable for a later invoice (deferred, not lost —
  labeled its real month, [§1.4](#14-when-a-late-approved-visit-gets-billed-and-under-which-month) first arm).

**Displayed invoice period comes from the rows, not the scan.** Set `PaymentInvoice.start_date` from
the **attributed row months** (`min` of `CompletedWorkInvoice.month`, [§1.3](#13-creation--snapshot--bump-watermark-atomic)
step 4), so the period matches what's actually billed:

- a late-delta-only invoice shows the billing month (not January);
- a catch-up invoice that includes a genuinely old never-billed work honestly shows that old month.

**Good to have (optional):** when a user-edited `start_date` leaves first-billing works before it,
show a non-blocking notice (e.g. *"N approved unit(s) before <start_date> are not on this invoice and
remain billable"*) — guards the "no automated runs + always-current start" edge where deferred
first-billing work could sit unbilled indefinitely.

### 1.3 Creation — snapshot + bump watermark (atomic)

On invoice creation (`InvoiceForm.save`, `tasks.py` auto-generate), in a transaction:

1. Compute delta per billable work.
2. Create one `CompletedWorkInvoice` row per work (`billed_count = delta`, frozen amounts/rate,
   `month` per [§1.4](#14-when-a-late-approved-visit-gets-billed-and-under-which-month)).
3. Set `invoiced_approved_count = saved_approved_count` for each billed work.
4. Set `PaymentInvoice.amount/amount_usd` = sum of the new rows, and `start_date` = `min` of their
   `month` so the displayed period matches what was billed (`end_date` stays the billing cutoff —
   [§1.4](#14-when-a-late-approved-visit-gets-billed-and-under-which-month) reads it in step 2).

> **Concurrency:** `select_for_update()` serializes two concurrent creations for the same opp and stops a
> double-billed delta (the reject guard reads the watermark under the same lock).

- **`amount` is server-computed (step 4) — the source of truth.** Safe: the manual form's
  service-delivery `amount` is a **read-only, auto-computed** field (`forms.py:1747`), not
  user-entered; computing it from the rows at submit also closes the render-to-submit window. (Custom
  invoices are user-entered but link no works → no snapshot rows.)
- **Per-row amount.** `amount_local = billed_count × payment_unit.amount`; `amount_usd = amount_local
  / rate`, where `rate` is for the row's `month` ([§1.4](#14-when-a-late-approved-visit-gets-billed-and-under-which-month):
  billing-month rate for a late delta, approval-month rate for a first billing). `exchange_rate`
  stores that `ExchangeRate` row.
- **Display.** `get_invoiced_visit_items` becomes a `GROUP BY payment_unit, month` aggregation over
  `invoice.work_items` — same shape as today but frozen → **drift eliminated**.

### 1.4 When a late-approved visit gets billed, and under which month

**When:** on the **next invoice** after the approval — the watermark catches it
(`saved_approved_count > invoiced_approved_count`) regardless of the work's age. Late approvals never
wait for a matching "period."

**Which month** — decided by the watermark (no date math):

```
month = TruncMonth(status_modified_date)   if invoiced_approved_count == 0   # first billing
        TruncMonth(invoice.end_date)        if invoiced_approved_count  > 0   # late delta
```

- **First billing** → the work's real approval month (a January work first billed in March still
  shows under January, so catch-up months stay accurate).
- **Late delta** → the invoice's billing month — because `status_modified_date` is stale: it only
  moves on a *status* change (`models.py:783-787`), and a late duplicate keeps the status `approved`,
  so the date is frozen at the original approval.

**Why the watermark branch, not a date clamp:**

- The date alone can't classify a delta as "late" — a late duplicate's stale `status_modified_date`
  looks exactly like an old first-billing work.
- The watermark records "already billed" directly — also why late deltas bypass the date window
  ([§1.2](#12-selection--delta-based)) and the displayed period uses row months, not the stale date.
- `invoice.end_date` is nullable but always set for service-delivery invoices (the only kind with
  rows), so the late-delta arm always has a date.

### 1.5 Late approval & rejection guard

- **Late approval:** `CompletedWorkUpdater` keeps recomputing `saved_approved_count` (it rises);
  display reads frozen snapshots, so the current invoice is unaffected and the next invoice bills the
  delta. No special handling.
- **Rejection guard:** a locked work (`invoiced_approved_count > 0`) can't have its approved visits
  rejected. Enforced via a single `completed_work_is_locked(work)` helper called from every reject
  write path — see [Rejection-guard enforcement](#shared--rejection-guard-enforcement).

### 1.6 Lifecycle — cancel / reject / pay

- **Cancel / reject** (`CANCELLED_BY_NM`, `REJECTED_BY_PM`): replace `unlink_completed_works` with a
  step that atomically deletes this invoice's `CompletedWorkInvoice` rows and decrements each
  affected work's `invoiced_approved_count` by that row's `billed_count`. Works whose billed total
  returns to 0 become rejectable and fully re-billable.
- **Paid:** terminal for money; no clawback (rejection already blocked); rows immutable.
- **Archived:** out of scope — a one-off historical management command
  (`archive_pending_and_submitted_invoices.py`, hardcoded 2025-11-01 cutoff) bulk-sets `status →
  ARCHIVED` with no unlink/rollback. No watermark rollback added; archived invoices' works stay
  billed.

### 1.7 Edge cases

- **Multiple late approvals across cycles:** each invoice bills `saved − invoiced`; the watermark
  advances monotonically; no double-bill.
- **Cancel then re-bill:** cancelling decrements the watermark by *that invoice's* `billed_count`
  ([§1.6](#16-lifecycle--cancel--reject--pay)), not a blanket reset.
  - Single covering invoice → cancel brings it to 0 → full count re-billable.
  - Several covering invoices (original + deltas) → cancelling one drops the watermark to the
    **remaining** billed total; only that invoice's portion re-bills. Reaches 0 only when every
    covering invoice is cancelled.
- **Pre-invoice rejection:** watermark 0 → allowed → count may drop → fine.
- **Child/optional deliver units:** `saved_approved_count` already encodes the min-across-required /
  child-constrained count (`completed_work.py:99-128`); the watermark tracks that number, so deltas
  inherit it with no extra logic.

### 1.8 Backfill

Populate `CompletedWorkInvoice` rows for every existing invoice, so legacy invoices render from the
new table once the FK is gone:

- For each existing invoice with linked works (read via the still-present `CompletedWork.invoice`
  FK), create one row per work from the work's **current** `saved_*` and set
  `invoiced_approved_count = saved_approved_count`. Use `month = TruncMonth(status_modified_date)` and
  `exchange_rate = get_exchange_rate(currency, month)` — matching how `get_invoice_items`
  (`utils/completed_work.py:330,352`) groups and prices line items today, so backfilled rows reproduce
  the current display exactly. (This is §1.4's first-billing arm; only *late deltas* use `end_date`.)
- **Pre-existing drift is frozen as-is, not corrected.** For an already-drifted legacy invoice
  `Σ(rows)` equals the *current* line items, not necessarily the original `PaymentInvoice.amount`.
  The design stops *future* drift; reconciling history is a non-goal and is generally impossible (the
  original per-work split is gone). Surfacing which invoices carry drift is optional — see
  [Good to have — legacy drift report](#good-to-have--legacy-drift-report).

### 1.9 Releases — expand/contract rollout

The FK drop ships in a *separate, later release*, not with the cutover — safe under rolling deploys
(old pods still reference the FK mid-deploy), preserves clean rollback (a dropped column can't be
restored), and keeps a replicated-column drop (`multidb`) as its own deliberate step.

**Release 1 — expand (cutover, keep the FK):**

1. **Schema add** — add `CompletedWorkInvoice`; add `CompletedWork.invoiced_approved_count`
   (default 0). Leave `CompletedWork.invoice` in place.
2. **Backfill** — run [§1.8](#18-backfill).
3. **Code cutover** — re-point every `CompletedWork.invoice` reader to `CompletedWorkInvoice` +
   watermark, then stop reading/writing the FK (leave the column for old pods / rollback):
   - `get_uninvoiced_completed_works_qs` → the [§1.2](#12-selection--delta-based) billable query.
   - `get_start_date_for_invoice` → watermark predicate ([§1.2](#12-selection--delta-based)).
   - `link_invoice_to_completed_works` → [§1.3](#13-creation--snapshot--bump-watermark-atomic)
     snapshot + watermark bump.
   - `unlink_completed_works` → [§1.6](#16-lifecycle--cancel--reject--pay) per-invoice rollback.
   - `get_invoiced_visit_items` → [§1.3](#13-creation--snapshot--bump-watermark-atomic) aggregation
     over `invoice.work_items`.
   - deliver-status "invoiced?" badge → existence of any `CompletedWorkInvoice` row for the work.

**Release 2 — contract (after Release 1 is stable in prod):**

4. **Drop the FK** — confirm nothing reads `CompletedWork.invoice` and totals are stable, then drop
   the column.

---

## Shared — Rejection-guard enforcement

**Why the guard is needed.** A billed work can also hold an approved-but-not-yet-agreed visit, and
the count includes *all* approved visits. That visit is still rejectable (`reject_visits` only blocks
`review_status=agree`, `views.py:1123`), so rejecting it drops `saved_approved_count` and **drifts
the already-billed invoice**. The guard stops this: once a work is billed, none of its approved
visits can be rejected.

**Enforcement.** A single `completed_work_is_locked(work)` predicate (Approach 1 checks the
watermark; the appendix checks the FK + live status), called from both reject paths:

- `reject_visits` (`views.py:1111`) — bulk reject.
- `bulk_update_visit_review_status` (`visit_import.py`) — CSV reject.

**PM disagree needs no guard:** an agreed visit can never be disagreed (`views.py:1483`,
`visit_import.py:760-762`), so disagree can't reduce a billed work's count.

**Covers the `auto_approve_payments` cascade too:** there, rejecting any one visit flips the whole
work to `rejected` (`completed_work.py:142-147`). Blocking the rejection at the source means that
flip never fires — no separate whole-work guard needed.

---

## Good to have — legacy drift report

Optional, not required for correctness. The backfill ([§1.8](#18-backfill)) freezes legacy rows from
current `saved_*`, so an already-drifted invoice is frozen *with* its drift. To avoid blessing that
silently, add a read-only report (management command / query, not a migration step):

- For each existing invoice, compare `Σ(CompletedWorkInvoice.amount)` against `PaymentInvoice.amount`.
- Where they differ, emit invoice number, opportunity, and delta. **Report only — no auto-correct**
  (history is a non-goal; the original per-work split can't be reconstructed).

Expected to be a small minority — drift only arises where a post-invoice approve/reject changed
`saved_*` after the amount froze.

---

## Open questions (pending product)

**1. Must late approvals be billed? — RESOLVED: yes.** Product confirmed that duplicate approvals are
a requirement, so we must account for new approvals post-invoice for a completed work. We proceed
with Approach 1 and reject the appendix alternative; the snapshot + watermark work
([§1.1](#11-model-changes)–[§1.3](#13-creation--snapshot--bump-watermark-atomic)) is required. (The
drift fix and rejection lock are shared regardless and can be built first.)

**2. Bill on approval or on PM agreement?** Once a completed work has its required visits both
**approved** and **agreed**, any later duplicate set needs only to be **approved** to be counted
toward billing — the PM never has to agree to it. What's the reason for this? Shouldn't the duplicate
visits also be agreed before they bill? Pre-existing behavior, not introduced by this design. *If
billing must require agreement:* [§1.2](#12-selection--delta-based)'s billable count changes.

*Code path:* the count ignores `review_status` (`completed_work.py:83-86`, `:100-102`); the agree-gate
is `_is_delivery_approved` (`completed_work.py:157-161`); `review_status=agree` is set only by the PM
flow `user_visit_review` (`views.py:1477`).

---

## Appendix — Approach 2: Full lock (rejected)

> **Rejected.** Drops late approvals — once a work is invoiced, a later approval is never billed,
> which Open question 1 (resolved) rules out: a worker approved after the invoice issues would lose
> that payment. Kept only as a cheaper fallback (no new tables, no watermark) if that requirement
> ever reverses. **Do not implement as-is.**

**Core idea — freeze by skipping.** Guard `CompletedWorkUpdater` to **skip works linked to a live
invoice**, so their `saved_*` never recompute and today's display already matches the frozen
`amount`. No snapshot table, no watermark.

**Differences from Approach 1:**

- **No delta:** selection stays `invoice__isnull=True`, and a late approval is **dropped** (guard
  `approve_visits` / `visit_import` to refuse counting onto a locked work) — the opposite of
  forward-bill, and why it's rejected.
- **Locked predicate** is FK + live status instead of the watermark (same rule, same enforcement).
- **No rollback:** lifecycle keeps `unlink_completed_works` as-is, and retains the
  `CompletedWork.invoice` FK that Approach 1 drops.
