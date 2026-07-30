# Legacy Invoice Drift Report

A **read-only** management command. For each automated service-delivery invoice it reconstructs
what was billed at generation time and compares it to what is owed
today (current agreement-gated rules). The difference is the **drift**. It writes two CSVs
(one per invoice, one per completed work) plus a summary. Nothing in the database is changed.

## Two kinds of drift

### 1. Late-delivery drift (under-billed)

Deliveries that were approved _after_ the invoice was generated, and so were never billed.

Each completed work is linked to exactly one invoice, and only deliveries approved on/before the
generation date were billed. Later approvals are never picked up by a future invoice.

- Columns: `late_delivery_units`, `late_delivery_drift`.
- Positive drift — potentially correctable.

### 2. Over-billing drift (over-billed)

Deliveries that _were_ billed but are not owed today — duplicates counted at billing time by
approval status that were never PM-agreed (or were later disagreed).

- Columns: `overbilled_units`, `overbilled_drift`.
- Negative drift — the amount was already billed/paid, so it cannot be reliably corrected.

> **Both only occur on works that allow duplicates** (more than one delivery). The first delivery
> needs a PM "agree" for the work to be billable at all; duplicate deliveries were counted by
> approval status alone. So only the duplicates can diverge from today's agreed count.

## How drift is computed

1. **Reconstruct the billed count** per work as of the invoice's _generation date_ using the old status-only rule — before CCCT-2505 restricted counts to PM-agreed visits. This is a **best estimate** of what was actually billed.
2. **Desired count today** = the work's current agreement-gated `approved_count`.
3. **Drift = desired − reconstructed**: positive → late / under-billed, negative → over-billed.
   Drift amounts are valued at the full per-unit rate (FLW amount + org amount).

## Confidence check: `reconstruction_gap`

`reconstruction_gap = frozen invoice amount − reconstructed FLW amount`. It tells us whether the
reconstruction even reproduces the original invoice, so we know how far to trust the drift numbers:

- **≈ 0** → reconstruction reproduces the invoice exactly (FLW-era); trust the drift.
- **≈ `org_pay_drift`** → the invoice already billed org pay (generated after the org-pay fix); org pay is not actually missing.
- **anything else** → reconstruction can't reproduce the frozen amount — review.

An invoice is only reported when it has late or over-billed units, or an _unexplained_ gap.

## Edge case: visits with no approval date

Some approved visits have a `NULL` status_modified_date (a data-quality gap — the approval
timestamp was never recorded). We can't place these in time, so we assume they were billed and
count them; excluding them would manufacture phantom late-delivery drift. If that assumption is
wrong the reconstruction over-counts, `reconstruction_gap` goes non-zero and flags the row, and
`legacy_null_status_date_visits` shows how many such visits are involved.

## Org pay (informational only)

`org_pay_drift` = reconstructed units × org rate — org pay omitted on billed units before the
CCCT-2470 fix. Reported for visibility; on its own it does **not** mark an invoice as drifting
(the same treatment we gave org pay previously).

## Next steps (decision this report informs)

1. **No correction** — communicate to the delivery team as needed. Consistent with how we handled
   org pay omitted from invoices. Simple and safe.
2. **Positive-drift correction** — a backfill could roll positive (late) drift into a future
   invoice. Moderately complex.

> Negative (over-billing) drift cannot be reliably corrected.

Spec is available here: https://github.com/dimagi/commcare-connect/blob/36b3c25d462cf594b0e472936cbc273526c059a7/docs/superpowers/specs/2026-06-08-invoice-line-item-locking-design.md#good-to-have--legacy-drift-report.
