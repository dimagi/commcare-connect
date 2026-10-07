# Tech Spec: Scheduled Pay Rate Changes

- **Jira:** CCCT-2884 (epic CCCT-2883)
- **PRD:** "PRD: Scheduled Pay Rate Changes" (Mary Rocheleau)
- **Design:** Claude design "Scheduled Pay Rate Changes"
- **Status:** Draft

## 1. Summary

Program Managers (PMs) can change a payment unit's pay rate (worker pay and/or org pay) without starting a new
opportunity. A change is **scheduled** and takes effect on the **1st of the next month**. Until then it can be
edited or cancelled.

A pay rate change only affects work **approved on or after** the date it takes effect. Work approved before that keeps
its old pay rate.

Pay rates move to a new **pay rate history** table, which becomes the single source of truth for what a payment unit pays.

## 2. Goals

- PMs can schedule, edit and cancel a pay rate change on any payment unit.
- PMs can see the active pay rate, any upcoming change, and the full history (what changed, when, by whom).
- When worker pay changes, workers get an app notification and Network Managers (NMs) see a banner.
- When only org pay changes, only NMs see a banner.
- Each invoice shows one pay rate per payment unit per month.
- A pay rate change never changes pay for work that is already approved.

## 3. Out of scope

- Fixing past invoices that went out at a wrong pay rate.
- Changing visit limits or budget caps.
- Non-managed opportunities. In practice all opportunities are managed.
- Custom effective dates. A change always takes effect on the 1st of the month.
- Different pay rates for different workers in the same period.
- Changes to the worker mobile app.

## 4. The problem today

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

So if we simply changed the pay rate on the payment unit, the next recalculation would reprice all past approved work,
and the invoice on the 2nd would bill last month at the new pay rate. That is exactly what this feature must not do.

## 5. Solution overview

1. **Pay rate history is the single source of truth.** A new table keeps one row per pay rate, with the date it
   takes effect. The pay rate on any day is the latest row that started on or before that day.
2. **No job is needed to apply a change.** Because the pay rate is looked up by date, a scheduled pay rate simply becomes
   active on its date.
3. **Add pay, don't recalculate it.** When work is recalculated, only units approved since the last calculation are
   paid, at the pay rate active that day. Pay already earned is never touched.
4. **Invoices use the pay rate for the invoice month.**
5. **Remove `amount` and `org_amount` from `PaymentUnit`** once everything reads from the pay rate history.

## 6. Data model

### 6.1 New model: `PaymentUnitRate`

```python
class PaymentUnitRate(BaseModel):
    payment_unit = models.ForeignKey(PaymentUnit, on_delete=models.CASCADE, related_name="pay_rates")
    flw_amount = models.PositiveIntegerField()  # worker pay
    org_amount = models.PositiveIntegerField(default=0)  # org pay
    effective_from = models.DateField()  # always the 1st of a month (or the opportunity start date)
    cancelled_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    cancelled_on = models.DateTimeField(null=True, blank=True)

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
and `date_modified`. These cover "set by" and "changed on" in the history table.

A pay rate's **status** is worked out, not stored:

| Status            | Rule                                                        |
| ----------------- | ----------------------------------------------------------- |
| Scheduled         | not cancelled, `effective_from` is in the future            |
| Active            | the latest non-cancelled pay rate with `effective_from` ≤ today |
| Ended             | an older non-cancelled pay rate                                 |
| Cancelled         | `cancelled_on` is set                                       |
| Opportunity Start | the first pay rate (shown as a label in history)                |

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

### 6.3 Existing data

A data migration creates one `PaymentUnitRate` per existing payment unit, using its current `amount` and
`org_amount`, with `effective_from` set to the opportunity's start date. This becomes the "Opportunity Start" row
in the history.

New payment units get their first pay rate row when they are created.

This backfill is needed whatever product decides about existing opportunities (open question 4), because the pay
rate history is the only place pay rates are read from.

### 6.4 Removing `amount` and `org_amount` from `PaymentUnit`

Two steps, so nothing breaks in between:

1. **This feature:** every place that reads `payment_unit.amount` or `payment_unit.org_amount` switches to the pay
   rate history (`current_pay_rate`, `pay_rate_on(day)` or the subquery above). Creating or editing a payment unit writes a pay
   rate row. The old fields are still filled in when a unit is created but are no longer read.
2. **Follow-up:** drop the two fields with a migration.

Places that read the pay rate today include pay calculation, invoices, budget totals and claim limits, the payment unit
form and table, the add-budget forms, the mobile API serializer, and exports.

## 7. Scheduling a change

**Who:** the same PMs who can edit payment units today. No new permissions.

**Rules:**

- The effective date is always the 1st of next month. The PM does not choose it.
- A payment unit can have **only one** scheduled change at a time. Editing it updates the same row, so if several
  people edit it, the last edit wins. `modified_by` records who edited it last.
- Cancelling sets `cancelled_by` and `cancelled_on`. The row stays in the history, marked "Cancelled".
- A change can be edited or cancelled until its effective date.
- The new pay rate must differ from the active pay rate in at least one of worker pay or org pay.
- **Blocked if the opportunity ends before the effective date.** Show: "This opportunity ends before <date>, so a
  pay rate change can't be scheduled."
- **Budget check:** see section 11.

```python
def schedule_pay_rate_change(payment_unit, flw_amount, org_amount, user):
    effective_from = first_day_of_next_month(date.today())
    pay_rate, _ = PaymentUnitRate.objects.update_or_create(
        payment_unit=payment_unit,
        effective_from=effective_from,
        cancelled_on__isnull=True,
        defaults={"flw_amount": flw_amount, "org_amount": org_amount, "modified_by": user},
        create_defaults={
            "flw_amount": flw_amount,
            "org_amount": org_amount,
            "created_by": user,
            "modified_by": user,
        },
    )
    return pay_rate
```

## 8. Notifications

| What changed    | Workers (app notification) | NMs (Connect Web banner) |
| --------------- | -------------------------- | ------------------------ |
| Worker pay      | Yes                        | Yes                      |
| Org pay only    | No                         | Yes                      |
| Both            | Yes                        | Yes                      |

**Worker notification:** a new Celery beat task runs **monthly, on the 1st at 00:00 UTC**. It finds pay rates
that take effect that day where worker pay changed, and sends a push notification through the existing messaging service
to all workers with access to the opportunity. Example text: "From October 1, you earn 1.25 USD per verified
visit."

```python
@celery_app.task()
def notify_workers_of_pay_rate_changes():
    starting_today = PaymentUnitRate.objects.filter(cancelled_on__isnull=True, effective_from=date.today())
    for pay_rate in starting_today.select_related("payment_unit"):
        previous = pay_rate.payment_unit.pay_rate_on(pay_rate.effective_from - timedelta(days=1))
        if previous and previous.flw_amount != pay_rate.flw_amount:
            send_pay_rate_change_notification(pay_rate)
```

The task only sends messages. Pay does not depend on it. If a run is missed, workers miss the message but are still
paid correctly.

**NM banner:** see section 12.4. It is worked out from the rate history, so it needs no task.

## 9. Paying for work (the key change)

Today, every recalculation does:

```python
saved_payment_accrued = approved_count * payment_unit.amount
```

New rule: **only add pay for newly approved units**, at the pay rate active today.

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

In managed opportunities `approved_count` only counts visits that are both approved **and** agreed, so pay is added
when the PM agrees visits.

Effects:

- Agreeing new visits, imports and the nightly job only add pay for new units. Past pay is never repriced.
- The rest of the system (worker totals, reports, exports) already reads these stored amounts, so it needs no
  change.
- The `CompletedWork.payment_accrued` property, which multiplies by the current pay rate, should read the stored amount
  instead.

Trade-off: we can no longer rebuild an amount from scratch if it ever goes wrong. Since all opportunities are
managed and agreed visits can't be rejected, approved units only go up, which keeps this safe.

## 10. Invoices

Invoice line items use the pay rate **for the line item's month**, from `payment_unit.pay_rate_on(month_start)`, instead of
the payment unit's current pay rate.

```python
pay_rate = work.payment_unit.pay_rate_on(line_item_month)
flw_pay = billed_count * pay_rate.flw_amount
org_pay = billed_count * pay_rate.org_amount
```

- The invoice on October 2 billing for the September month uses the September pay rate, even though October's pay rate is
  already active.
- A late approval on old work is billed in the month it is billed, at that month's pay rate. This matches the "add pay
  at the pay rate active today" rule in section 9, so invoices and worker pay agree.
- Already-created invoice line items store their amounts and are not affected.

## 11. Budget

### 11.1 How budget works today

- Each worker gets a **visit limit** when they join. It is saved and never changes on its own.
- **Claimed budget** = every worker's visit limit × (worker pay + org pay).
- **Remaining budget** = total budget − claimed budget.
- A new worker can only join if the remaining budget covers a full worker's share.

### 11.2 What a pay rate change does

A pay rate change does **not** change existing workers' visit limits. It changes how much each visit costs, so it changes
how much budget is left for **new** workers.

- Pay rate goes **up**: claimed budget goes up, so fewer new workers can join.
- Pay rate goes **down**: claimed budget goes down, so more new workers can join.
- Adding budget gives the room back.

Changing the pay rate and changing the budget are independent, and the PM can do them in either order.

Example: total budget 10,000, 100 visits per worker.

| Situation                   | Claimed | Remaining | New workers possible |
| --------------------------- | ------- | --------- | -------------------- |
| 50 workers, pay rate 1.00   | 5,000   | 5,000     | 50                   |
| 50 workers, pay rate → 1.25 | 6,250   | 3,750     | 30                   |
| Then budget + 2,500         | 6,250   | 6,250     | 50                   |

### 11.3 The one rule: no overspend

If a pay rate increase would push remaining budget **below zero**, block it. Existing workers can still do all their
visits, so a negative remaining budget means the opportunity would pay out more than its total budget.

Example: 90 workers at pay rate 1.00 have claimed 9,000 of a 10,000 budget. A change to 1.25 would make claimed budget
11,250, which is 1,250 over. The PM sees: "This pay rate needs 1,250 more budget. Add budget first, then schedule the
change."

Pay rate decreases are never blocked.

```python
def can_afford_pay_rate_change(payment_unit, new_flw_amount, new_org_amount):
    current = payment_unit.current_pay_rate
    increase_per_visit = (new_flw_amount + new_org_amount) - (current.flw_amount + current.org_amount)
    if increase_per_visit <= 0:
        return True
    remaining_visits = remaining_visits_for_payment_unit(payment_unit)  # Σ (visit limit − visits already paid)
    return increase_per_visit * remaining_visits <= payment_unit.opportunity.remaining_budget
```

Only **remaining** visits are counted at the new pay rate, because visits already paid keep their old pay rate.

### 11.4 Claimed budget after a pay rate change

Claimed budget should be worked out as:

```
pay already earned (at the pay rates that applied)  +  remaining visits × current pay rate
```

and not `visit limit × current pay rate`. Otherwise a pay rate change makes past spending look bigger or smaller than it
really was.

## 12. UI

Based on the Claude design.

### 12.1 Payment unit edit page

- **Worker pay and org pay are read-only** on the form (open question 2). They move to a new **Pay Rates** card
  with the note: "Rates cannot be changed in place. A scheduled change takes effect on the first day of the
  following month and never affects work already approved."
- The card shows:
  - **Active** pay rate: worker pay, org pay, "Effective since <date>", "Set by <user> on <date>".
  - **Scheduled** pay rate (if any): worker pay, org pay, "Takes effect <date>", "Scheduled by <user> on <date>", with
    **Edit** and **Cancel Change** buttons. Cancel Change asks for confirmation, then marks the change "Cancelled".
  - **Schedule Rate Change** button. It is **disabled** with a tooltip when a change is already scheduled ("A change
    is already scheduled for <date>. Edit or cancel it instead.") or when the opportunity ends before the next
    1st.
- **Rate History** table below the form: Effective, Worker Pay, Org Pay, Changed By, Changed On, Status. Cancelled
  changes stay in the list, marked "Cancelled", with the user who cancelled them.

### 12.2 Schedule Rate Change modal

- Fields: **New worker pay per visit**, **New org pay per visit**, each showing the current pay rate underneath.
- **Effective date**: shown, not editable (1st of next month).
- Summary text: "Visits approved before <date> are paid at <old pay rate>. Connect Workers are notified in the app when
  the new rate takes effect. This change can be cancelled any time before <date>."
- Buttons: **Cancel** (closes the modal without saving) and **Schedule Change**.
- The same modal, pre-filled, is used for **Edit**.
- Budget error (section 11) is shown inside the modal.

### 12.3 Payment units list

Add an **Upcoming Change** column showing what changes and when, for example:

- "Worker pay 1.00 → 1.25 (USD) from October 1, 2026"
- "Org pay 0.50 → 0.75 (USD) from October 1, 2026"
- "None"

If both change, show both lines. The org pay line follows the same visibility as the existing Org Pay column.

### 12.4 Banner for NMs

Shown on **every Connect Web page** to members of the opportunity's organisation when a change takes effect in **2
days or fewer**. Because it appears outside the opportunity, it names the opportunity:

"**<Opportunity name>**: a new rate for Per verified visit takes effect October 1, 2026. Worker pay changes from
1.00 to 1.25 (USD). View Details"

- Shown for both worker pay and org pay changes.
- If several changes are coming, show one line per opportunity and payment unit.
- "View Details" links to the payment unit edit page.
- No new banner table. The banner is shown whenever a scheduled pay rate exists with `effective_from − today ≤ 2 days`.

## 13. Edge cases

| Case                                            | Behaviour                                                                                 |
| ----------------------------------------------- | ----------------------------------------------------------------------------------------- |
| Change scheduled on the last day of the month   | Takes effect the next day. The banner shows for less than 2 days. Accepted.               |
| Several PMs edit the scheduled change           | Last edit wins. History shows the last editor.                                            |
| Cancel, then schedule again in the same month   | Allowed. The cancelled row stays in history and a new scheduled row is created.           |
| Opportunity ends before the effective date      | Scheduling is blocked (section 7).                                                        |
| Payment unit with child payment units           | Each unit has its own pay rate. Changing a parent does not change its children.           |
| Exchange rate                                   | USD amounts use the exchange rate on the day the units are paid, as today.                |
| Pay rate set to 0                               | Allowed (same as today). Worth a confirmation prompt in the modal.                        |
| Worker mobile app                               | Out of scope. **TODO:** check how the app shows the pay rate today and after a change.    |

## 14. Open questions

1. **Budget (product).** When a pay rate goes up, existing workers keep their visit limits and fewer new workers can
   join. If the increase would take the opportunity over its total budget, we block it until the PM adds budget
   (section 11).
   - Is blocking the right call in that case?
   - When you say a pay rate increase "decreases the visits available", do you mean visits for **new** workers only (our
     assumption), or should **existing** workers' visit limits also go down?

2. **Editing pay rates on the payment unit form (product).** Today a PM can change worker pay and org pay directly on
   the payment unit form, and the change applies straight away to everything. Should those two fields become
   read-only, so the only way to change a pay rate is "Schedule Rate Change"?

3. **Brand-new payment units (product).** The PRD says that a new payment unit with no submissions can have its
   pay rate changed immediately. Does this mean that once we create a payment unit with a certain pay rate, and this unit
   has no visits yet (since it is a new one), we allow the pay rate change to be effective immediately?

4. **Existing opportunities (product).** The PRD says this feature may only apply to new opportunities. Should
   opportunities that are already running also be able to schedule pay rate changes? (We move their current pay rates into
   the history table either way.)

5. **Nightly recalculation job (tech).** A job recalculates pay for every worker every night. After this change it
   only adds pay for newly approved work, so it's harmless. Do we still need it? This is separate from this feature.

## 15. Implementation tickets (proposed, to be created after approval)

1. **Pay rate history model and migration.** Add `PaymentUnitRate`, the `pay_rate_on` / `current_pay_rate` helpers,
   backfill one row per existing payment unit, and create the first row when a payment unit is created.
2. **Read pay rates from the history, and invoices use the month's pay rate.** Switch every reader of
   `PaymentUnit.amount` / `org_amount` to the pay rate history (pay, budget, claim limits, forms, tables, API,
   exports). Invoice line items look up the pay rate for the line item's month. Tests for the invoice run on the 2nd
   after a change on the 1st.
3. **Add-only pay calculation.** Change pay recalculation to add pay only for newly approved units, and make
   `CompletedWork.payment_accrued` read the stored amount. Tests that past pay does not change after a pay rate
   change.
4. **Schedule, edit and cancel.** Backend logic and permissions, the one-scheduled-change rule, the opportunity end
   date check, and the budget check (after open question 1 is answered).
5. **Worker notification task.** Monthly Celery beat task on the 1st, with its periodic task migration.
6. **Payment unit edit page UI.** Pay Rates card, Rate History table, Schedule/Edit modal, disabled button with
   tooltip, read-only pay rate fields (after open question 2 is answered).
7. **Upcoming changes: list column and NM banner.** "Upcoming Change" column on the payment units list, and the
   site-wide banner during the 2 days before a change.
8. **Drop `amount` and `org_amount` from `PaymentUnit`** (follow-up).

### 15.1 Deployment order

| Order | Tickets   | Why                                                                                        |
| ----- | --------- | ------------------------------------------------------------------------------------------ |
| A     | 1 + 2     | Must ship together: reads switch to the history, so the backfill has to be there.          |
| B     | 3         | Can ship with step A or on its own, but must be live before the first change is scheduled. |
| C     | 4 + 5 + 6 | Together they make up the scheduling feature PMs see. 4 can also ship first, then 5 + 6.   |
| D     | 7         | Needs 4. Can ship with step C or right after.                                              |
| E     | 8         | Only once step A has been live and stable.                                                 |

Nothing PMs can see changes until step C. Steps A and B change how pay and invoices are worked out, but give the
same results while no pay rate change exists.
