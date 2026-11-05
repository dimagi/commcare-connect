from datetime import timedelta
from io import StringIO

import pytest
from django.apps import apps
from django.core.management import call_command
from django.utils.timezone import now

from commcare_connect.opportunity.models import SYSTEM_USER, PaymentUnitRate, VisitValidationStatus
from commcare_connect.opportunity.tests.factories import (
    CompletedWorkFactory,
    DeliverUnitFactory,
    OpportunityFactory,
    PaymentUnitFactory,
    UserVisitFactory,
)
from commcare_connect.opportunity.utils.pay_rates import backfill_pay_rates


def _days_from_today(days):
    return now().date() + timedelta(days=days)


def _backfill():
    # Units get their Initial Pay Rate on creation, so remove the rows to act like units from before this feature
    PaymentUnitRate.objects.all().delete()
    return backfill_pay_rates(apps)


@pytest.mark.django_db
class TestBackfillPayRates:
    @pytest.mark.parametrize(
        "unit_start, opp_start, visit_status, expected",
        [
            (-5, -10, None, -5),  # unit start in the past: kept
            (None, -10, None, -10),  # no unit start: opportunity start
            (+5, -10, None, +5),  # not started yet: unit start
            (+5, -10, VisitValidationStatus.trial, +5),  # trial visits are never paid
            # Start date moved later after the unit started: today. Only possible for existing data;
            # CCCT-3014 stops PMs from changing the start date once a unit has started.
            (+5, -10, VisitValidationStatus.approved, 0),
            (+5, -10, VisitValidationStatus.pending, 0),
            (+5, -10, VisitValidationStatus.over_limit, 0),  # a PM can still approve it
        ],
    )
    def test_planned_for(self, unit_start, opp_start, visit_status, expected):
        opportunity = OpportunityFactory(start_date=_days_from_today(opp_start))
        unit = PaymentUnitFactory(
            opportunity=opportunity,
            start_date=None if unit_start is None else _days_from_today(unit_start),
        )
        if visit_status:
            UserVisitFactory(
                opportunity=opportunity,
                deliver_unit=DeliverUnitFactory(payment_unit=unit),
                status=visit_status,
                completed_work=None,
            )

        assert _backfill() == 1

        rate = unit.pay_rates.get()
        assert rate.planned_for == _days_from_today(expected)
        assert rate.applied_on is not None
        assert rate.created_by == rate.modified_by == SYSTEM_USER

    @pytest.mark.parametrize(
        "link, expected",
        [
            # A deliver unit can move to a new opportunity's unit, keeping its visits from the old opportunity
            ("other_opportunity", 5),
            # The deliver unit was removed from the unit after the visit was paid through it
            ("removed", 0),
        ],
    )
    def test_visits_linked_outside_the_deliver_unit(self, link, expected):
        unit = PaymentUnitFactory(start_date=_days_from_today(5))
        removed = link == "removed"
        UserVisitFactory(
            opportunity=unit.opportunity if removed else OpportunityFactory(),
            deliver_unit=DeliverUnitFactory(payment_unit=None if removed else unit),
            status=VisitValidationStatus.approved,
            completed_work=CompletedWorkFactory(payment_unit=unit) if removed else None,
        )

        _backfill()

        assert unit.pay_rates.get().planned_for == _days_from_today(expected)

    def test_copies_amounts_including_zero(self):
        paid = PaymentUnitFactory(amount=4, org_amount=1)
        unpaid = PaymentUnitFactory(amount=0, org_amount=0)

        _backfill()

        assert [(r.flw_amount, r.org_amount) for r in (paid.pay_rates.get(), unpaid.pay_rates.get())] == [
            (4, 1),
            (0, 0),
        ]

    def test_covers_ended_and_child_units(self):
        ended = PaymentUnitFactory(
            opportunity=OpportunityFactory(start_date=_days_from_today(-60), end_date=_days_from_today(-30))
        )
        parent = PaymentUnitFactory()
        child = PaymentUnitFactory(opportunity=parent.opportunity, parent_payment_unit=parent)

        assert _backfill() == 3

        for unit in (ended, parent, child):
            assert unit.pay_rates.count() == 1

    def test_only_fills_units_without_a_row(self):
        kept = PaymentUnitFactory()
        missing = PaymentUnitFactory()
        kept_rate = kept.pay_rates.get()
        missing.pay_rates.all().delete()

        assert backfill_pay_rates(apps) == 1
        assert backfill_pay_rates(apps) == 0

        assert kept.pay_rates.get().pk == kept_rate.pk
        assert missing.pay_rates.count() == 1

    @pytest.mark.parametrize(
        "args, rows_after, message",
        [
            ([], 1, "Created 1 pay rate rows."),
            (["--dry-run"], 0, "Would create 1 pay rate rows."),
        ],
    )
    def test_command_fills_units_created_during_deploy(self, args, rows_after, message):
        unit = PaymentUnitFactory()
        unit.pay_rates.all().delete()
        out = StringIO()

        call_command("backfill_pay_rates", *args, stdout=out)

        assert unit.pay_rates.count() == rows_after
        assert message in out.getvalue()
