from django.utils.timezone import now

BACKFILL_BATCH_SIZE = 1000
# Trial visits get no completed work, so they are never paid. A string, since migrations get historical models
# without the enum.
TRIAL_VISIT_STATUS = "trial"


def backfill_pay_rates(apps, dry_run=False):
    """Give each payment unit without a pay rate row one applied row from its current pay.

    Takes an app registry so a data migration can run it with historical models. Safe to run again: units that
    already have a row are skipped. Returns the number of rows created, or that would be created with dry_run.
    """
    PaymentUnit = apps.get_model("opportunity", "PaymentUnit")
    PaymentUnitRate = apps.get_model("opportunity", "PaymentUnitRate")
    UserVisit = apps.get_model("opportunity", "UserVisit")

    today = now().date()
    applied_on = now()
    units = PaymentUnit.objects.filter(pay_rates__isnull=True).select_related("opportunity").order_by("pk")
    created = 0
    batch = []
    for unit in units.iterator(chunk_size=BACKFILL_BATCH_SIZE):
        planned_for = unit.start_date or unit.opportunity.start_date
        # A unit that hasn't started has only trial visits. Payable visits mean its start was moved later after it
        # started, so it needs a pay rate from today for the approvals still to come.
        if planned_for > today and _has_payable_visits(UserVisit, unit):
            planned_for = today
        batch.append(
            PaymentUnitRate(
                payment_unit=unit,
                flw_amount=unit.amount,
                org_amount=unit.org_amount,
                planned_for=planned_for,
                applied_on=applied_on,
                # Not SYSTEM_USER: the data migration runs this and must keep the value it was written with
                created_by="system",
                modified_by="system",
            )
        )
        if len(batch) == BACKFILL_BATCH_SIZE:
            created += _save(PaymentUnitRate, batch, dry_run)
            batch = []
    if batch:
        created += _save(PaymentUnitRate, batch, dry_run)
    return created


def _save(PaymentUnitRate, batch, dry_run):
    if dry_run:
        return len(batch)
    return len(PaymentUnitRate.objects.bulk_create(batch))


def _has_payable_visits(UserVisit, unit):
    return (
        UserVisit.objects.filter(opportunity_id=unit.opportunity_id, deliver_unit__payment_unit=unit)
        .exclude(status=TRIAL_VISIT_STATUS)
        .exists()
    )
