from django.utils.timezone import now

from commcare_connect.utils.itertools import batched

BACKFILL_BATCH_SIZE = 1000


def backfill_pay_rates(apps, dry_run=False):
    """Give each payment unit without a pay rate row one row from its current pay.

    Takes an app registry so a data migration can run it with historical models. Safe to run again: units that
    already have a row are skipped. Returns the number of rows created, or that would be created with dry_run.
    """
    PaymentUnit = apps.get_model("opportunity", "PaymentUnit")
    PaymentUnitRate = apps.get_model("opportunity", "PaymentUnitRate")

    today = now().date()
    units = PaymentUnit.objects.filter(pay_rates__isnull=True).select_related("opportunity").order_by("pk")
    rates = (_pay_rate_for(PaymentUnitRate, unit, today) for unit in units.iterator(chunk_size=BACKFILL_BATCH_SIZE))
    created = 0
    for batch in batched(rates, BACKFILL_BATCH_SIZE):
        created += _save(PaymentUnitRate, batch, dry_run)
    return created


def _pay_rate_for(PaymentUnitRate, unit, today):
    return PaymentUnitRate(
        payment_unit=unit,
        flw_amount=unit.amount,
        org_amount=unit.org_amount,
        # A rate starts when its unit is created: workers can join before the unit starts, and budget checks need a
        # pay rate. The unit's start date can be later than its creation, so it isn't used. PaymentUnit stores no
        # created date, so the opportunity's start date stands in for it, or today if the opportunity hasn't started.
        planned_for=min(unit.opportunity.start_date, today),
        # Hardcoded rather than const SYSTEM_USER, so the data migration always writes the same value
        created_by="system",
        modified_by="system",
    )


def _save(PaymentUnitRate, batch, dry_run):
    if dry_run:
        return len(batch)
    return len(PaymentUnitRate.objects.bulk_create(batch))
