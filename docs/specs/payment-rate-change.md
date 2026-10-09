# Tech Spec: Scheduled Pay Rate Changes

- **Jira:** CCCT-2884 (epic CCCT-2883)
- **PRD:** "PRD: Scheduled Pay Rate Changes" (Mary Rocheleau)
- **Design:** Claude design "Scheduled Pay Rate Changes"
- **Status:** Draft

## 1. Summary

Program Managers (PMs) can change a payment unit's pay rate (worker pay and/or org pay). This works for all active
opportunities, including ones already running. A change is **scheduled** and takes effect on the **1st of the next
month**. Until then it can be edited or cancelled. For a payment unit with no visits yet, the change takes effect
immediately.

A pay rate change only affects work **approved on or after** the date it takes effect. Work approved before that keeps
its old pay rate.

When a pay rate goes up, workers' remaining visits go down so the opportunity stays within its budget. The PM sees
a warning before confirming and can add budget instead.

Pay rates move to a new **pay rate history** table, which becomes the single source of truth for what a payment unit
pays.

## 2. Goals

- PMs can schedule, edit and cancel a pay rate change on any payment unit.
- PMs can see the active pay rate, any upcoming change, and the full history (what changed, when, by whom).
- When worker pay changes, workers get an app notification on the day the new pay rate takes effect, and Network
  Managers (NMs) see a banner on Connect Web from 2 days before.
- When only org pay changes, only NMs see the banner (from 2 days before).
- Each invoice shows one pay rate per payment unit per month.
- A pay rate change never changes pay for work that is already approved.
- A pay rate increase never takes the opportunity over its budget.

## 3. Out of scope

- Fixing past invoices that went out at a wrong pay rate.
- Changing the opportunity's total budget automatically.
- Custom effective dates. A change always takes effect on the 1st of the month, except for a payment unit with no
  visits, where it takes effect immediately (section 7.1).
- Different pay rates for different workers in the same period.
- Changes to the worker mobile app.

## 4. The problem today

Today a PM can change the pay rate on the payment unit edit page, but the change is **retroactive**: it applies
straight away, including to visits already done and approved. We want a new pay rate to apply only to visits
approved from the 1st of the next month.

Pay is not calculated once and kept. Each completed-work record stores its earned amount, but that amount is
**recalculated from scratch** as:

```
approved units × current pay rate
```

This happens often:

- when a PM agrees or disagrees visits (all of that worker's records are recalculated, not just the agreed one)
- after visit and review imports
- every night, for every worker in every active auto-approve opportunity
- when an approved record receives another visit

Invoices also read the current pay rate when they are created. The automatic invoice runs on the **2nd** of each month
and bills the previous month.

We want pay already earned, and invoices for past months, to keep the pay rate that applied at the time.

## 5. Solution overview

1. **Pay rate history is the single source of truth.** A new table keeps one row per pay rate, with the date it
   takes effect. The pay rate on any day is the latest row that started on or before that day.
2. **Add to earned pay, don't recalculate it.** Each worker's earned (accrued) pay is only increased for units
   approved since the last update, at the pay rate active that day. Earned pay already recorded is never changed.
3. **Invoices use the pay rate for the invoice month.**
4. **Visit limits follow the budget.** When the cost per visit goes up, each worker's remaining visits are scaled
   down so the money left for them stays the same.
5. **Remove `amount` and `org_amount` from `PaymentUnit`** once everything reads from the pay rate history.

## 6. Data model

### 6.1 New model: `PaymentUnitRate`

```python
class PaymentUnitRate(BaseModel):
    payment_unit = models.ForeignKey(PaymentUnit, on_delete=models.PROTECT, related_name="pay_rates")
    flw_amount = models.PositiveIntegerField()  # worker pay
    org_amount = models.PositiveIntegerField(default=0)  # org pay
    effective_from = models.DateField()  # the 1st of a month; first pay rate: section 6.3; immediate: section 7.1
    cancelled_by = models.CharField(max_length=255, blank=True)  # user's email, or "system"
    cancelled_on = models.DateTimeField(null=True, blank=True)
    cancellation_reason = models.TextField(blank=True)  # filled in when the system cancels a change

    class Meta:
        constraints = [
            # At most one live (not cancelled) pay rate per payment unit per date
            models.UniqueConstraint(
                fields=["payment_unit", "effective_from"],
                condition=Q(cancelled_on__isnull=True),
                name="unique_live_pay_rate_per_date",
            ),
        ]
```

`BaseModel` already gives us `created_by` (who scheduled it), `modified_by` (who edited it last), `date_created`
and `date_modified`. These cover "set by" and "changed on" in the history table. `created_by`, `modified_by` and
`cancelled_by` store the user's email as text, so the record survives if the user is later deleted. When the system
cancels a change (section 11.5), `cancelled_by` is set to `"system"` and `cancellation_reason` says why. The history
shows it as cancelled by System.

A pay rate's **status** is worked out, not stored:

| Status            | Rule                                                                        |
| ----------------- | --------------------------------------------------------------------------- |
| Scheduled         | not cancelled, `effective_from` is in the future                            |
| Active            | the latest non-cancelled pay rate with `effective_from` ≤ today             |
| Ended             | an older non-cancelled pay rate                                             |
| Cancelled         | `cancelled_on` is set                                                       |
| Initial Rate      | the first pay rate of a payment unit (label in the history's Status column) |

### 6.2 Looking up a pay rate

```python
class PaymentUnit(models.Model):
    def pay_rate_on(self, day):
        return (
            self.pay_rates.filter(cancelled_on__isnull=True, effective_from__lte=day)
            .order_by("-effective_from")
            .first()
        )

    @property
    def current_pay_rate(self):
        return self.pay_rate_on(date.today())
```

Where pay rates are used inside database queries (for example, budget totals), use a subquery:

```python
current_pay_rate = PaymentUnitRate.objects.filter(
    payment_unit=OuterRef("payment_unit"),
    cancelled_on__isnull=True,
    effective_from__lte=date.today(),
).order_by("-effective_from")

OpportunityClaimLimit.objects.annotate(
    flw_amount=Subquery(current_pay_rate.values("flw_amount")[:1]),
    org_amount=Subquery(current_pay_rate.values("org_amount")[:1]),
)
```

### 6.3 First pay rate of a payment unit

Every payment unit needs a first pay rate row, shown as "Initial Rate" in the history. Its `effective_from` must be
on or before the first approval it covers, otherwise looking up the pay rate for that work finds nothing.

New payment units get this row when they are created. `effective_from` is the unit's `start_date` if set, otherwise
today.

### 6.4 Data migration for existing payment units

Create one row per existing payment unit from its current `amount` and `org_amount`. `effective_from` is:

- the payment unit's `start_date`, if set. Visits submitted before it are marked "trial" and never paid, so no
  approval comes before it.
- otherwise, the opportunity's start date. All work happens after the opportunity starts.

The feature applies to all active opportunities, so every existing payment unit needs this row. The pay rate history
is also the only place pay rates are read from.

### 6.5 Changing a payment unit's start date

**Why this matters.** The Initial Rate starts on the payment unit's start date (section 6.3). Every approval under
the unit must fall on or after that date, otherwise there is no pay rate to look up. Today a PM can edit
`start_date` at any time, which could break this.

**Rule.** A PM can change `start_date` only if:

- the payment unit **hasn't started yet**: its current `start_date` is in the future, or it has no visits at all, and
- the new date is **after today**, or **today** if the unit has no visits at all.

When the date changes, the Initial Rate's `effective_from` moves with it in the same save.

Otherwise, show: "This payment unit has already started, so its start date can't be changed."

**Why this is safe.** Before a unit starts, visits are only "trial" visits, which are never paid. So there is no
approved work for the Initial Rate to cover yet, and it can move freely.

**Why not today or earlier.** Visits processed earlier today, while the start date was still in the future, are
already marked trial. Moving the start date to today (or into the past) would put those visits inside the unit's
active period while they stay trial, which is misleading. If the unit has no visits at all, there is nothing to
contradict, so today is allowed. A past date is never allowed: it has the same effect as today, but the history would
show an Initial Rate that started before anything could use it.

**Example.** Today is November 5. The unit starts on November 10.

| Change                              | Allowed?          | Why                                                                     |
| ----------------------------------- | ----------------- | ----------------------------------------------------------------------- |
| Move to November 20                 | Yes               | Not started yet. The Initial Rate moves to November 20.                 |
| Move to November 6                  | Yes               | Not started yet, and the new date is after today.                       |
| Move to November 5 (today)          | Only if no visits | Visits processed earlier today would be trial inside the active period. |
| Move to November 3                  | No                | The new date is in the past.                                            |
| On November 12, move to November 20 | No                | Already started. Visits approved November 10–12 would have no pay rate. |

### 6.6 Removing `amount` and `org_amount` from `PaymentUnit`

Two steps, so nothing breaks in between:

1. **This feature:** every place that reads `payment_unit.amount` or `payment_unit.org_amount` switches to the pay
   rate history (`current_pay_rate`, `pay_rate_on(day)` or the subquery above). Creating or editing a payment unit writes a pay
   rate row. The old fields are still filled in when a unit is created but are no longer read.
2. **Follow-up:** drop the two fields with a migration.

Places that read the pay rate today include pay calculation, invoices, budget totals and claim limits, the payment unit
form and table, the add-budget forms, the mobile API serializer, and exports.

### 6.7 Protecting pay rate history

Pay rate rows are a record of what workers and organisations were paid, so they are never deleted in normal use.

- **No delete in the app.**
- **`on_delete=PROTECT`** on `payment_unit`, so deleting a payment unit that has pay rate history fails instead of
  silently removing it.
- **Django admin:** if the model is registered, it is read-only and cannot be deleted.

**Exception: deleting a whole opportunity.** `delete_opportunity` (used for stale and test opportunities) deletes the
opportunity's payment units on purpose. Because of `PROTECT`, `PaymentUnitRate` must be added to its list of models to
delete, just before `PaymentUnit`. The dev-only `generate_sample_data` command needs the same change.

## 7. Scheduling a change

**Who:** PM users with admin access to the opportunity, the same users who can edit payment units today. No new
permissions.

**When it takes effect**

- On the 1st of next month. The PM does not choose the date.
- Two exceptions:
  - the payment unit has no visits: the change takes effect immediately (section 7.1)
  - the payment unit hasn't started yet: the Initial Rate is changed in place (section 7.2)

**Editing**

- A payment unit can have **only one** scheduled change at a time.
- Editing updates the same row, so if several people edit it, the last edit wins. `modified_by` records who edited
  it last.
- A change can be edited until its effective date.

**Cancelling**

- Only a **scheduled** change can be cancelled, until its effective date.
- Cancelling sets `cancelled_by` and `cancelled_on`. The row stays in the history, marked "Cancelled".
- The backend refuses to cancel the Initial Rate (even while it shows as Scheduled, section 7.2) or a pay rate that is
  already active, so this doesn't rely on the UI hiding the button.

**When a change is blocked**

- The new pay rate must differ from the active pay rate in at least one of worker pay or org pay.
- **The opportunity ends before the effective date.** Show: "This opportunity ends before `<Effective date>`, so a pay rate
  change can't be scheduled." This never applies to a payment unit with no visits, because its change takes effect
  today.
- **The cost per visit goes up and the opportunity can't fund it** (section 11.5).

**Warnings**

- **Visit limit warning:** shown when the cost per visit goes up (section 11.4).

```python
def schedule_pay_rate_change(payment_unit, flw_amount, org_amount, user):
    if payment_unit.start_date and payment_unit.start_date > date.today():
        effective_from = payment_unit.start_date  # not started yet: edits the Initial Rate in place (section 7.2)
    elif not has_visits(payment_unit):
        effective_from = date.today()  # section 7.1
    else:
        effective_from = first_day_of_next_month(date.today())
    pay_rate, _ = PaymentUnitRate.objects.update_or_create(
        payment_unit=payment_unit,
        effective_from=effective_from,
        cancelled_on__isnull=True,
        defaults={"flw_amount": flw_amount, "org_amount": org_amount, "modified_by": user.email},
        create_defaults={
            "flw_amount": flw_amount,
            "org_amount": org_amount,
            "created_by": user.email,
            "modified_by": user.email,
        },
    )
    return pay_rate


def cancel_pay_rate_change(pay_rate, user):
    is_initial_rate = not pay_rate.payment_unit.pay_rates.filter(
        cancelled_on__isnull=True, effective_from__lt=pay_rate.effective_from
    ).exists()
    if is_initial_rate or pay_rate.effective_from <= date.today():
        raise ValidationError("Only a scheduled pay rate change can be cancelled.")
    pay_rate.cancelled_by = user.email
    pay_rate.cancelled_on = now()
    pay_rate.save(update_fields=["cancelled_by", "cancelled_on"])
```

### 7.1 Payment units with no visits

If a payment unit has **no visits at all** (any status), a pay rate change takes effect **immediately** instead of
on the 1st. Nobody has done any work under it, so there is nothing to protect. (If the unit hasn't started yet,
section 7.2 applies instead.)

**What the PM sees**

- The same Schedule Rate Change modal. The effective date shows "Immediately" instead of the 1st.
- Before saving, the PM must confirm: "No visits have been recorded for this payment unit yet, so this pay rate takes
  effect immediately and can't be cancelled. Continue?" If worker pay changes, it adds "Workers will be notified
  now." If the cost per visit goes up, the visit limit warning (section 11.4) is shown in the same confirmation.
- No NM banner, because there is nothing upcoming. The new pay rate shows straight away on the Pay Rates card and in
  the payment units list.

**What happens on save**

- `effective_from` is set to today. A second change on the same day updates the same row.
- If the cost per visit goes up, the limits of workers who have already joined are scaled straight away (section
  11.2). There are no visits, so no funding check is needed.
- If worker pay changed, workers are notified straight away.
- A payment unit with no visits never has a scheduled change, because every change to it takes effect immediately.

**Avoiding race conditions**

- The "has visits" check runs inside the same transaction as the save, after locking the opportunity row and the
  payment unit's claim limit rows.
- Without the lock, a visit could arrive and be auto-approved at the old pay rate, while this month's invoice uses
  the new one. A worker joining at the same moment could also get a limit at the old cost that is never scaled.
- With the lock, a visit or a new worker either lands first (and the change is refused or the worker is scaled) or
  waits until the change is saved.

```python
def apply_immediate_pay_rate_change(payment_unit, flw_amount, org_amount, user):
    with transaction.atomic():
        # Lock first, then check for visits, so nothing can slip in between:
        # - a worker joining locks the opportunity; without this they could get a limit at the old cost
        #   that the scaling below misses
        # - form processing locks the claim limit rows before saving a visit; without this a visit could
        #   arrive and be auto-approved at the old pay rate, while invoices for this month use the new one
        Opportunity.objects.select_for_update().get(pk=payment_unit.opportunity_id)
        list(OpportunityClaimLimit.objects.select_for_update().filter(payment_unit=payment_unit))
        if has_visits(payment_unit):
            raise ValidationError("This payment unit now has visits. Schedule the change for the 1st instead.")
        previous = payment_unit.current_pay_rate
        pay_rate = schedule_pay_rate_change(payment_unit, flw_amount, org_amount, user)
        scale_visit_limits(payment_unit, previous, pay_rate)
        if previous.flw_amount != pay_rate.flw_amount:
            transaction.on_commit(partial(send_pay_rate_change_notification.delay, pay_rate.pk))
```

### 7.2 Payment units that haven't started yet

If a payment unit's `start_date` is in the future, its Initial Rate starts on that date (section 6.3), so it isn't
active yet.

- In the Pay Rates card and history, the Initial Rate shows as **Scheduled** ("Takes effect `<Start date>`").
- A pay rate change **edits the Initial Rate in place**, the same way a scheduled change is edited: the last edit
  wins, and `modified_by` records who edited it last.
- There is **no Cancel** button, only Edit, and the backend refuses to cancel it too (section 7). Cancelling would
  leave the unit with no pay rate.
- The modal's effective date shows the unit's start date.
- Workers who have already joined get limits for this unit, so if the cost per visit goes up their limits are scaled
  when the change is saved (section 11.2). The unit has no paid visits yet, so there is no shortfall.
- Workers are not notified and there is no NM banner, because the unit hasn't started. (We can't reliably tell if
  the Initial Rate was edited, since moving the start date also updates the row, so the banner is never shown for
  it.)

## 8. The job on the 1st

A new Celery beat task runs **monthly, on the 1st at 00:00 UTC**. For every pay rate that takes effect that day, it:

1. **Checks the change can be funded** if the cost per visit went up. If not, it cancels the change before it takes
   effect and tells the PM (section 11.5).
2. **Adjusts visit limits** if the cost per visit went up (section 11.2).
3. **Notifies workers** if worker pay changed.

The monthly job only finds today's changes and starts **one task per pay rate**. Each task does all its work in one
transaction, so if it fails nothing is half-done, and a retry starts clean without touching other pay rates. (If the
whole job retried instead, pay rates already handled would have their limits scaled twice.)

```python
@celery_app.task()
def apply_pay_rate_changes():
    starting_today = PaymentUnitRate.objects.filter(
        cancelled_on__isnull=True,
        effective_from=date.today(),
        date_created__date__lt=date.today(),  # immediate changes (section 7.1) are applied when saved
    )
    for pay_rate_id in starting_today.values_list("pk", flat=True):
        apply_pay_rate_change.delay(pay_rate_id)


@celery_app.task(
    base=ReportFinalFailureToSentry,  # see below
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=5,
)
def apply_pay_rate_change(pay_rate_id):
    pay_rate = PaymentUnitRate.objects.select_related("payment_unit").get(pk=pay_rate_id)
    payment_unit = pay_rate.payment_unit
    previous = payment_unit.pay_rate_on(pay_rate.effective_from - timedelta(days=1))
    with transaction.atomic():
        # Same locks as an immediate change (section 7.1): no worker can join and no visit can arrive
        # between checking the shortfall and scaling the limits
        opportunity = Opportunity.objects.select_for_update().get(pk=payment_unit.opportunity_id)
        list(OpportunityClaimLimit.objects.select_for_update().filter(payment_unit=payment_unit))
        shortfall = funding_shortfall(payment_unit, previous, pay_rate)
        if shortfall > opportunity.remaining_budget:
            cancel_unfunded_change(pay_rate, shortfall)  # also emails the PM
            return
        scale_visit_limits(payment_unit, previous, pay_rate)
        if previous.flw_amount != pay_rate.flw_amount:
            transaction.on_commit(partial(send_pay_rate_change_notification.delay, pay_rate.pk))
```

**If a task fails after all its retries,** it is reported to Sentry with the pay rate and opportunity, so someone can
follow up. `ReportFinalFailureToSentry` is a small Celery base class whose `on_failure` calls
`sentry_sdk.capture_exception`. Celery only calls `on_failure` once the retries are used up, so each failure is
reported once.

The locks are held per payment unit for only a moment, at 00:00 when traffic is low, so visits are not held up for
long.

The funding check has to run **before** any visit is agreed on the 1st, because pay rates are looked up by date and
the new pay rate is already active from 00:00. Running the job at 00:00 covers this.

Pay itself does not depend on this job. Visit limits do: if the job doesn't run, limits stay at the old level and
the opportunity can overspend (open question 4).

### 8.1 Notifications

| What changed    | Workers (app notification) | NMs (Connect Web banner) |
| --------------- | -------------------------- | ------------------------ |
| Worker pay      | Yes                        | Yes                      |
| Org pay only    | No                         | Yes                      |
| Both            | Yes                        | Yes                      |

**Worker notification:** a push notification through the existing messaging service, sent to all workers with
access to the opportunity. If their visit limit changed, the message says so. Example: "From `<Effective date>`, you
earn `<New worker pay>` `<Currency>` per `<Payment unit name>`. You have `<Visits left>` visits left."

**NM banner:** see section 12.4. It is worked out from the pay rate history, so it needs no job.

## 9. Updating earned pay (the key change)

Today, every recalculation does:

```python
saved_payment_accrued = approved_count * payment_unit.amount
```

New rule: **only add to earned pay for newly approved units**, at the pay rate active today.

```python
new_units = approved_count - completed_work.saved_approved_count
if new_units > 0:
    pay_rate = completed_work.payment_unit.current_pay_rate
    new_pay = new_units * pay_rate.flw_amount
    new_org_pay = new_units * pay_rate.org_amount
    exchange_rate = get_exchange_rate(currency, now())

    completed_work.saved_payment_accrued += new_pay
    completed_work.saved_payment_accrued_usd += new_pay / exchange_rate
    completed_work.saved_org_payment_accrued += new_org_pay
    completed_work.saved_org_payment_accrued_usd += new_org_pay / exchange_rate
    completed_work.saved_approved_count = approved_count
```

In managed opportunities `approved_count` only counts visits that are both approved **and** agreed, so earned pay
goes up when the PM agrees visits.

Effects:

- Agreeing new visits, imports and the nightly job only add earned pay for new units. Earned pay already recorded
  is never repriced.
- The rest of the system (worker totals, reports, exports) already reads these stored amounts, so it needs no
  change.
- The `CompletedWork.payment_accrued` property, which multiplies by the current pay rate, should read the stored amount
  instead.

> **Note (trade-off):** we can no longer rebuild an amount from scratch if it ever goes wrong. Since all
> opportunities are managed and agreed visits can't be rejected, approved units only go up, which keeps this safe.

## 10. Invoices

Invoice line items use the pay rate **for the line item's month**, instead of the payment unit's current pay rate.

```python
def pay_rate_for_month(payment_unit, month):
    # Use the pay rate on the LAST day of the month, not the first. Scheduled changes always start on the 1st, so
    # for those the result is the same. But two cases start mid-month, and the first day would give the wrong answer:
    # - a payment unit or opportunity that starts mid-month has no pay rate yet on the 1st
    # - an immediate change for a payment unit with no visits (section 7.1): all of that month's work is approved
    #   after the change, so it must use the new pay rate
    return payment_unit.pay_rate_on(last_day_of_month(month))


pay_rate = pay_rate_for_month(work.payment_unit, line_item_month)
flw_pay = billed_count * pay_rate.flw_amount
org_pay = billed_count * pay_rate.org_amount
```

- The invoice on October 2 billing for the September month uses the September pay rate, even though October's pay rate is
  already active.
- A late approval on old work is billed in the month it is billed, at that month's pay rate. This matches the "add to
  earned pay at the pay rate active today" rule in section 9, so invoices and earned pay agree.
- Already-created invoice line items store their amounts and are not affected.

## 11. Budget and visit limits

### 11.1 How it works today

- Each payment unit has a **max visits per worker** (`max_total`).
- Each worker gets their own **visit limit** when they join: the payment unit's max. A worker can only join if the
  remaining budget covers their full share, otherwise they are refused.
- PMs can raise or lower an individual worker's limit later, so workers on the same payment unit can have different
  limits.
- **Claimed budget** = every worker's visit limit × (worker pay + org pay).
- **Remaining budget** = total budget − claimed budget.

### 11.2 What a pay rate increase does

Product decision: when the cost per visit goes up, visit limits go down for **both existing and new workers**, so
the opportunity stays within budget. This also applies when only org pay goes up.

**The idea**

Workers can have different limits and have used different amounts, so there is no single new limit for everyone.
Instead, each worker keeps the **same remaining money**, and we work out how many units it buys at the new pay rate.

**Units, not visits**

Pay is per **unit**: one completed set of the payment unit's required forms. The visit limit is checked per form, so
it effectively caps the number of units. That is why the rule below counts units.

**The rule**

Earned pay is added when a unit is **approved**, not when its forms are done. So a unit done in September but
approved in October earns the new pay rate. Only units **already approved** keep the old cost. Everything else
(units waiting for approval and units not yet done) is costed at the new pay rate.

Why: limits are scaled at 00:00 on the 1st, when the new pay rate is already active. So any unit still waiting for
approval will earn the new pay rate if it is approved. Some may be rejected and cost nothing, but we can't know which,
so we plan for the worst case: all of them are approved. If some are rejected, the worker simply has some money left
over.

```
scaled limit = units approved + floor((limit − units approved) × old cost ÷ new cost)
new limit    = the higher of: scaled limit, units completed
```

- **Cost** = worker pay + org pay, per unit.
- **Units approved** = units approved (agreed) before the change. Their earned pay is already recorded at the old
  pay rate.
- **Units completed** = units with all their forms submitted, whether waiting for approval or already approved.
- **Units already completed are never reduced**, because any of them could still be approved. If a worker's units
  waiting for approval cost more at the new pay rate than their remaining money, the difference is a **shortfall**
  (section 11.5).

**Example**

Pay rate goes from 1.00 to 1.25.

| Worker | Limit | Money allocated (limit × 1.00) | Approved | Waiting for approval | Money left (allocated − approved × 1.00) | Units it buys at 1.25 | Scaled limit (approved + units it buys) | New limit (higher of scaled limit and approved + waiting) |
| ------ | ----- | ------------------------------ | -------- | -------------------- | ---------------------------------------- | --------------------- | --------------------------------------- | --------------------------------------------------------- |
| Asha   | 100   | 100.00                         | 20       | 20                   | 80.00                                    | 64                    | 84                                      | 84                                                        |
| Ben    | 150   | 150.00                         | 10       | 0                    | 140.00                                   | 112                   | 122                                     | 122                                                       |
| Chen   | 100   | 100.00                         | 100      | 0                    | 0.00                                     | 0                     | 100                                     | 100                                                       |
| Dev    | 100   | 100.00                         | 10       | 90                   | 90.00                                    | 72                    | 82                                      | 100                                                       |

Dev's scaled limit is 82, but Dev has already completed 100 units (10 approved + 90 waiting for approval). Units
already completed are never reduced, so Dev's limit stays at 100. The 90 waiting units will cost 112.50 at the new
pay rate, but only 90.00 is left for Dev. The extra 22.50 is the **shortfall** (section 11.5).

**Payment unit with two forms**

A payment unit pays for a registration **and** a follow-up together, so one unit = one of each. A limit of 100 means
up to 100 registrations and 100 follow-ups, so at most 100 units.

A worker has done 40 registrations and 40 follow-ups, so 40 units are completed, and 20 of them are approved. The pay
rate goes from 1.00 to 1.25:

- scaled limit = 20 + floor(80 × 1.00 ÷ 1.25) = 20 + 64 = 84
- new limit = the higher of 84 and 40 = **84**

The worker can now do up to 84 registrations and 84 follow-ups.

**New workers**

The payment unit's **max visits per worker** (the limit a worker gets when they join) is scaled by the same ratio:

- 100 × 1.00 ÷ 1.25 = **80**

A worker who joins after the change gets 80 units, which costs the same as 100 units did before (80 × 1.25 = 100 ×
1.00 = 100.00). So new workers get the same money as existing ones.

**Code**

```python
def scale_visit_limits(payment_unit, old_rate, new_rate):
    old_cost = old_rate.flw_amount + old_rate.org_amount
    new_cost = new_rate.flw_amount + new_rate.org_amount
    if new_cost <= old_cost:
        return  # see open question 1
    if old_cost == 0:
        return  # raised from zero: funded from the remaining budget instead (section 11.8)
    counts = unit_counts_per_worker(payment_unit)  # {claim_limit_id: (approved, completed)}, from CompletedWork
    for claim_limit in payment_unit.opportunityclaimlimit_set.all():
        approved, completed = counts.get(claim_limit.id, (0, 0))
        scaled = approved + (claim_limit.max_visits - approved) * old_cost // new_cost
        claim_limit.max_visits = max(completed, scaled)
        claim_limit.save(update_fields=["max_visits"])
    if payment_unit.max_total:
        payment_unit.max_total = payment_unit.max_total * old_cost // new_cost
        payment_unit.save(update_fields=["max_total"])
```

### 11.3 When limits change

Visit limits are scaled on the **1st**, when the new pay rate takes effect, not when the change is scheduled. (For a
payment unit with no visits, straight away, see section 7.1.)

- Until the 1st, visit limits stay the same.
- Units approved between scheduling and the 1st earn the old pay rate, and are counted as approved when limits are
  scaled.
- If the change is cancelled before the 1st, limits were never touched, so there is nothing to undo.

### 11.4 Warning before confirming

When scheduling, we can't know which pending visits will be agreed before the 1st. So the warning assumes the worst
case: all pending visits are paid at the new pay rate. Any visit agreed before the 1st is paid at the old pay rate,
so the real cut on the 1st is the same or smaller.

The warning is one message for the payment unit, with no per-worker numbers. Each worker's remaining visits drop by
at most `1 − old cost ÷ new cost`:

"This increase will reduce workers' remaining visits for this payment unit by up to `<Percent>`% when it takes
effect on `<Effective date>`. Add budget first if you want workers to keep their visits." (`<Percent>` =
1 − old cost ÷ new cost, e.g. 20% for 1.00 → 1.25.)

### 11.5 No overspend: funding check

A shortfall (like Dev's above) is money the opportunity would owe beyond what was set aside for that worker. It can
be paid from the opportunity's **remaining budget**, which is still within the total budget. If the remaining budget
can't cover it, the change must not happen.

The shortfall is checked twice:

1. **When scheduling** (using today's numbers): if the shortfall is more than the remaining budget, block the change.
   Message: "Some workers have more pending visits than their remaining budget covers at the new pay rate. Agree their
   pending visits or add budget first."
2. **On the 1st, at 00:00** (before scaling limits): if the shortfall is now more than the remaining budget (for
   example, workers submitted many visits in between, or new workers used up the remaining budget), the change is
   **cancelled automatically**. The old pay rate stays. The row stays in the history as "Cancelled" with
   `cancellation_reason` filled in, and the PM is emailed. The PM can schedule it again once pending visits are agreed
   or budget is added.

Agreeing pending visits before the 1st always reduces the shortfall, because those visits are then paid at the old pay
rate.

Between scheduling and the 1st, PMs and NMs see a warning banner as soon as the change can no longer be funded
(section 12.4), so they have time to act before it is cancelled.

This is a proposal for product to confirm (open question 2).

### 11.6 Adding or reducing visits from the Add Budget form

The Add Budget form has two parts. Both keep working as today; only the cost per unit they use changes. Before a change
is scheduled, nothing is different.

Example: pay rate goes from 1.00 to 1.25 on October 1. Max visits per worker is 100.

**Existing workers (increase or decrease visits)**

| When                           | What happens                                                                                       |
| ------------------------------ | -------------------------------------------------------------------------------------------------- |
| Between scheduling and the 1st | "+10 visits" adds 10 × 1.00 = 10.00 of budget. Visits approved before the 1st earn the old pay rate. On the 1st, whatever is not yet approved is scaled like the rest of the worker's money. If none of the 10 are approved by then, the worker ends up with 8 extra visits, not 10. |
| After the 1st                  | "+10 visits" adds 10 × 1.25 = 12.50 of budget, and the worker gets 10 extra visits.                |

Decreasing visits works the same way, and the existing rule that a limit can't go below the visits already done still
applies.

**New workers (number of Connect Workers)**

| When                           | What happens                                                                                       |
| ------------------------------ | -------------------------------------------------------------------------------------------------- |
| Between scheduling and the 1st | "+10 workers" adds 10 × (100 × 1.00) = 1,000.00 of budget. Workers who join before the 1st get 100 visits, scaled to 80 on the 1st. Workers who join after the 1st get the new max of 80. Either way, the budget still funds 10 workers. |
| After the 1st                  | "+10 workers" adds 10 × (80 × 1.25) = 1,000.00 of budget, and each worker gets 80 visits.          |

A worker's share of money stays the same (100.00) before and after the change. Only the number of visits it buys
changes.

**Note shown on the form.** While a change is scheduled, the Add Budget form shows: "A pay rate change takes effect on
`<Effective date>`. Visits not approved by then, including any added now, will be adjusted to the new pay rate."

### 11.7 Claimed budget

Today claimed budget = visit limit × current cost. After a pay rate change, that would price units already approved
at the new pay rate, making it look like less budget is left than there really is, and wrongly blocking new workers from
joining. So claimed budget **must** be worked out as:

```
pay already earned (at the pay rates that applied)  +  (visit limit − units approved) × current cost per unit
```

### 11.8 Special case: pay rate raised from zero

If worker pay and org pay are both 0 (cost 0.00) and the PM raises either of them, limits are **not scaled**.

**Why.** Scaling keeps each worker's remaining money the same. With a cost of 0, that money is 0.00, so the rule in
11.2 would freeze every worker at their units completed and set the max for new workers to 0. Raising the pay rate
would stop all further work on the payment unit. While the cost was 0, no money was set aside for this payment unit,
so its share is still in the opportunity's **remaining budget**. Raising the pay rate from 0 means "start paying for
this work", so it is funded from the remaining budget instead, like workers joining.

**Rule**

- Visit limits and the payment unit's max visits per worker stay as they are.
- The funding check (section 11.5) uses a different amount: every worker's units not yet approved, at the new cost.

```
needed = Σ over workers of (limit − units approved) × new cost
```

- If the remaining budget covers it, the change goes ahead. If not, it is blocked when scheduling, or cancelled on
  the 1st, as in section 11.5. The message asks the PM to add budget first.
- The modal shows how much budget the change needs, instead of the visit limit warning.

**Example.** Worker pay goes from 0.00 to 1.00. Asha has a limit of 100, has completed 30 units, and 10 are approved.
Asha keeps her limit of 100. The change needs (100 − 10) × 1.00 = 90.00 for Asha, plus the same for every other
worker, from the remaining budget.

## 12. UI

Screenshots from the Claude design are included below. This section only lists what the screenshots don't show.

### 12.1 Payment unit edit page

_Screenshot: Payment Unit Edit page with the Pay Rates card and Rate History._

- Worker pay and org pay are no longer on the form. They are only shown, read-only, in the Pay Rates card.
- Cancel Change asks for confirmation first.
- For a payment unit that hasn't started yet, the Initial Rate shows as Scheduled with Edit only, no Cancel Change
  (section 7.2).
- **Schedule Rate Change** is **disabled** with a tooltip:
  - when a change is already scheduled: "A change is already scheduled for `<Effective date>`. Edit or cancel it instead."
  - when the opportunity ends before the next 1st (for a payment unit with no visits, only if the opportunity has
    ended)
- The funding warning (section 12.4) is also shown on the Pay Rates card.

### 12.2 Schedule Rate Change modal

_Screenshot: Schedule Rate Change modal._

- The same modal, pre-filled, is used for **Edit**.
- **Effective date** is shown, not editable: the 1st of next month, "Immediately" for a payment unit with no visits
  (section 7.1), or the unit's start date if it hasn't started yet (section 7.2).
- When the cost per visit goes up, the modal shows the visit limit warning (section 11.4).
- If the change can't be funded, the modal shows the funding error (section 11.5) and the change is not saved.

### 12.3 Payment units list

_Screenshot: Payment Units list with the Upcoming Change column._

- The Upcoming Change column says what changes, for example "Worker pay 1.00 → 1.25 (USD) from October 1, 2026".
  If both worker pay and org pay change, show both lines.
- The org pay line follows the same visibility as the existing Org Pay column.

### 12.4 Banners

_Screenshot: pay rate change banner._

**Upcoming change banner (NMs)**

- Shown on **every Connect Web page**, not just the edit page, to members of the opportunity's organisation, when a
  change takes effect in **2 days or fewer**. Because of that it names the opportunity:

  "`<Opportunity name>`: a new rate for `<Payment unit name>` takes effect `<Effective date>`. Worker pay changes
  from `<Old worker pay>` to `<New worker pay>` (`<Currency>`)."

  For an org pay change, the second sentence says "Org pay changes from … to …". If both change, it shows both.
- If several changes are coming, one line per opportunity and payment unit.
- Not shown for an Initial Rate (section 7.2).

**Funding warning banner (PMs and NMs)**

Not in the design. If a scheduled change can no longer be funded from the budget left (section 11.5), a warning
banner is shown **straight away**, not only in the last 2 days, so there is time to act:

"`<Opportunity name>`: the pay rate change for `<Payment unit name>` on `<Effective date>` can no longer be funded
from the budget left. Agree pending visits or add budget before `<Effective date>`, or this change will not
apply."

- Shown to the PMs who can schedule pay rate changes and to the NMs of the opportunity's organisation.
- It goes away by itself once the change can be funded again (pending visits agreed or budget added).
- No periodic job is needed. The shortfall is worked out when a page loads and cached per scheduled change (for
  example, for an hour), because it means counting visits per worker. So the warning can take up to an hour to
  appear or go away. There are only ever a few scheduled changes (those waiting for the 1st), so this stays cheap.

### 12.5 Add Budget form

Not in the design. While a change is scheduled, show the note from section 11.6.

## 13. All scenarios

| Case                                            | Behaviour                                                                                 |
| ----------------------------------------------- | ----------------------------------------------------------------------------------------- |
| Change scheduled on the last day of the month   | Takes effect the next day. The banner shows for less than 2 days.                         |
| Several PMs edit the scheduled change           | Last edit wins. History shows the last editor.                                            |
| Cancel, then schedule again in the same month   | Allowed. The cancelled row stays in history and a new scheduled row is created.           |
| Opportunity ends before the effective date      | Scheduling is blocked (section 7).                                                        |
| Worker has used all their visits                | Limit stays the same. Units already completed are never reduced.                          |
| Workers with different visit limits             | Each worker's remaining visits are scaled separately (section 11.2).                      |
| Change cancelled before the 1st                 | Nothing to undo. Limits only change on the 1st.                                           |
| New workers join before the 1st                 | They get limits at the old cost, then are scaled on the 1st like everyone else.           |
| Budget added before the 1st                     | Scaled down on the 1st like the rest of the worker's money (section 11.6).                |
| Change can't be funded on the 1st               | Cancelled automatically, old pay rate stays, PM is emailed (section 11.5).                |
| Payment unit with no visits                     | Change takes effect immediately (section 7.1).                                            |
| Payment unit hasn't started yet                 | Initial Rate shows as Scheduled, is edited in place, can't be cancelled (section 7.2).    |
| Payment unit with child payment units           | Each unit has its own pay rate. Changing a parent does not change its children.           |
| Pay rate set to 0                               | Allowed (same as today). Worth a confirmation prompt in the modal.                        |
| Pay rate raised from 0                          | Limits are not scaled; funded from the remaining budget (section 11.8).                   |

## 14. Open questions

**Answered**

- **Visit limits on a pay rate increase (product).** Reduce visit limits for both existing and new workers, with a
  warning before the PM confirms (section 11).
- **Org pay increases (product).** An org-pay-only increase also reduces workers' visit limits, since budget includes
  org pay.
- **Brand-new payment units (product).** If a payment unit has no visits, a pay rate change takes effect immediately
  (section 7.1).
- **Existing opportunities (product).** The feature applies to all active opportunities, including ones already
  running.

**Open**

1. **Pay rate decreases (product).** When the cost per visit goes **down**, should workers' remaining visits go
   **up** by the same rule, or stay as they are? This spec assumes they stay as they are.

2. **Changes that can't be funded (product).**

   **The situation.** A visit is paid at the pay rate active on the day it is **approved**, not the day it was done.
   So visits that are done in September but still waiting for approval on October 1 get the **new**, higher pay
   rate. That can cost more than was set aside for the worker.

   **Example.** The pay rate goes from 1.00 to 1.25 on October 1.
   - Dev has a limit of 100 visits, so 100 USD is set aside for Dev.
   - 10 of Dev's visits were approved in September and paid 10 USD. That leaves 90 USD for Dev.
   - Dev's other 90 visits are done but still waiting for approval on October 1. At 1.25 each they cost 112.50 USD.
   - That is **22.50 USD more** than is left for Dev.

   **What we propose:** see section 11.5. In short, the extra comes from the opportunity's unused budget, and if
   there isn't enough the change is blocked when scheduling, or cancelled automatically on the 1st.

   **Question:** is this OK? The alternative was to stop workers from submitting visits between scheduling and the 1st,
   which we think is too disruptive.

3. **"Initial Rate" label (product).** The design labels the first pay rate in the history "Opportunity Start".
   For a payment unit added after the opportunity started, that is slightly misleading, so this spec uses
   "Initial Rate" instead. Is that OK?

4. **Missed job run (tech).** If the job on the 1st fails after its retries, visit limits are not reduced and the
   opportunity can overspend. Should the job run daily and catch up on anything missed? That needs a field on
   `PaymentUnitRate` recording that limits were adjusted.

5. **Nightly recalculation job (tech).** A job recalculates pay for every worker every night. After this change it
   only adds pay for newly approved work, so it's harmless. Do we still need it? This is separate from this feature.

6. **Worker mobile app (tech).** Changes to the app are out of scope, but the app shows the pay rate today (it
   receives the payment unit's worker pay and a budget per visit / per worker).
   - **TODO:** check what the app shows today, and what workers will see after a pay rate change and after their visit
     limits are scaled.

## 15. Implementation tickets (proposed, to be created after approval)

1. **Pay rate history model and migration.** Add `PaymentUnitRate` (with `PROTECT`), the
   `pay_rate_on` / `current_pay_rate` helpers, backfill one row per existing payment unit, and create the first row
   when a payment unit is created. Add it to `delete_opportunity` and `generate_sample_data` (section 6.7).
2. **Restrict editing a payment unit's start date.** Only allow changing `start_date` while the unit hasn't started,
   and only to a date after today (today if the unit has no visits). When it changes, move the Initial Rate's
   `effective_from` to the new date in the same save (section 6.5).
3. **Read pay rates from the history, and invoices use the month's pay rate.** Switch every reader of
   `PaymentUnit.amount` / `org_amount` to the pay rate history (pay, budget, claim limits, forms, tables, API,
   exports). Invoice line items look up the pay rate for the line item's month. Tests for the invoice run on the 2nd
   after a change on the 1st.
4. **Add-only earned pay.** Change the earned pay calculation to add only for newly approved units, and make
   `CompletedWork.payment_accrued` read the stored amount. Tests that earned pay already recorded does not change
   after a pay rate change.
5. **Pay Rates card and Rate History (read-only).** On the payment unit edit page, show the Active pay rate and the
   Rate History table. Worker pay and org pay stay on the form for now, so PMs can still change them until ticket 6
   ships.
6. **Schedule, edit and cancel.** Backend and UI together: the Schedule Rate Change modal, the Scheduled pay rate on
   the card with Edit and Cancel Change, the disabled button with tooltip, permissions, the one-scheduled-change rule,
   the opportunity end date check, the funding check (including raising from zero, section 11.8), the visit limit
   warning, and changes for payment units that haven't started (section 7.2). Remove worker pay and org pay from the
   form.
7. **Job on the 1st.** Monthly Celery beat task with its periodic task migration, starting one retried task per pay
   rate (Sentry report on final failure): funding check (including section 11.8) and automatic cancellation (with PM
   email), scaling visit limits and the payment unit's max visits per worker, and the worker notification. Also
   switch claimed budget to the formula in section 11.7.
8. **Upcoming pay rate changes: list column, banners and Add Budget note.** "Upcoming Change" column on the payment
   units list, the NM banner during the 2 days before a change, the funding warning banner (shown as soon as a change
   can't be funded), and the note on the Add Budget form.
9. **Immediate change for payment units with no visits (section 7.1).** "Immediately" in the modal, the
   confirmation, the locking, and scaling limits and notifying workers on save. Until this ships, a unit with no
   visits is scheduled for the 1st like any other.
10. **Drop `amount` and `org_amount` from `PaymentUnit`** (follow-up).

### 15.1 Deployment order

| Order | Tickets   | Why                                                                                                        |
| ----- | --------- | ---------------------------------------------------------------------------------------------------------- |
| A     | 1 + 2 + 3 | Must ship together: reads switch to the history, so the backfill and the start date rule have to be there. |
| B     | 4         | Can ship with step A or on its own, but must be live before the first change is scheduled.                 |
| B     | 5         | Needs step A. Can ship any time after it.                                                                  |
| C     | 6 + 7     | Must ship together: they make up the scheduling feature, and limits must be scaled when a change applies.  |
| D     | 8         | Needs 6. Can ship with step C or right after.                                                              |
| D     | 9         | Needs 6. Can ship any time after step C.                                                                   |
| E     | 10        | Only once step A has been live and stable.                                                                 |

Apart from the start date restriction (ticket 2) and the read-only Pay Rates card (ticket 5), nothing PMs can see
changes until step C. Steps A and B change how pay and invoices are worked out, but give the same results while no
pay rate change exists.
