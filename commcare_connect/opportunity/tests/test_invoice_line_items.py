import datetime
from decimal import Decimal

import pytest

from commcare_connect.opportunity.models import CompletedWorkStatus
from commcare_connect.opportunity.tests.factories import (
    CompletedWorkFactory,
    ExchangeRateFactory,
    OpportunityAccessFactory,
    PaymentUnitFactory,
)
from commcare_connect.opportunity.utils.invoice_line_items import (
    _build_billable_rows,
    get_billable_completed_works_qs,
    get_billable_line_items,
    group_line_items,
)

JAN = datetime.date(2026, 1, 1)
JAN_END = datetime.date(2026, 1, 31)
FEB = datetime.date(2026, 2, 1)
FEB_END = datetime.date(2026, 2, 28)
MAR = datetime.date(2026, 3, 1)
MAR_END = datetime.date(2026, 3, 31)
JAN_APPROVAL = datetime.datetime(2026, 1, 15, tzinfo=datetime.UTC)


@pytest.fixture
def billing_setup(db):
    """A USD opportunity (exchange rate 1) with a payment unit worth 100 FLW pay + 20 org pay."""
    access = OpportunityAccessFactory()
    payment_unit = PaymentUnitFactory(opportunity=access.opportunity, amount=100, org_amount=20)
    ExchangeRateFactory(currency_code="USD", rate=1, rate_date=datetime.date(2020, 1, 1))
    return access, payment_unit


def approved_work(access, payment_unit, *, approved=1, invoiced=0, approved_on=JAN_APPROVAL):
    return CompletedWorkFactory(
        opportunity_access=access,
        payment_unit=payment_unit,
        status=CompletedWorkStatus.approved,
        saved_approved_count=approved,
        invoiced_approved_count=invoiced,
        status_modified_date=approved_on,
    )


@pytest.mark.django_db
class TestBillableSelection:
    def test_first_billing_work_inside_the_window_is_billable(self, billing_setup):
        access, payment_unit = billing_setup
        work = approved_work(access, payment_unit, approved_on=JAN_APPROVAL)

        qs = get_billable_completed_works_qs(access.opportunity, JAN, JAN_END)

        assert list(qs) == [work]

    def test_first_billing_work_outside_the_window_is_excluded(self, billing_setup):
        access, payment_unit = billing_setup
        approved_work(access, payment_unit, approved_on=JAN_APPROVAL)

        qs = get_billable_completed_works_qs(access.opportunity, FEB, FEB_END)

        assert not qs.exists()

    def test_late_delta_bypasses_the_window(self, billing_setup):
        """A late duplicate keeps status_modified_date at the original (January) approval, so the
        window can't sensibly apply — it must bill on the next invoice regardless."""
        access, payment_unit = billing_setup
        work = approved_work(access, payment_unit, approved=2, invoiced=1, approved_on=JAN_APPROVAL)

        qs = get_billable_completed_works_qs(access.opportunity, FEB, FEB_END)

        assert list(qs) == [work]

    def test_fully_billed_work_is_not_billable(self, billing_setup):
        access, payment_unit = billing_setup
        approved_work(access, payment_unit, approved=2, invoiced=2)

        assert not get_billable_completed_works_qs(access.opportunity, JAN, FEB_END).exists()

    def test_unapproved_work_is_not_billable(self, billing_setup):
        access, payment_unit = billing_setup
        CompletedWorkFactory(
            opportunity_access=access,
            payment_unit=payment_unit,
            status=CompletedWorkStatus.pending,
            saved_approved_count=1,
        )

        assert not get_billable_completed_works_qs(access.opportunity, JAN, FEB_END).exists()


@pytest.mark.django_db
class TestBillableRows:
    def test_prices_only_the_unbilled_delta_including_org_pay(self, billing_setup):
        access, payment_unit = billing_setup
        approved_work(access, payment_unit, approved=3, invoiced=1)

        (row,) = _build_billable_rows(access.opportunity, JAN, FEB_END)

        assert row.billed_count == 2
        assert row.flw_amount_local == Decimal("200")
        assert row.org_amount_local == Decimal("40")
        assert row.total_amount_local == Decimal("240")
        assert row.exchange_rate == Decimal("1")
        assert row.total_amount_usd == Decimal("240")

    def test_first_billing_is_attributed_to_its_approval_month(self, billing_setup):
        access, payment_unit = billing_setup
        approved_work(access, payment_unit, approved_on=JAN_APPROVAL)

        (row,) = _build_billable_rows(access.opportunity, JAN, FEB_END)

        assert row.month == JAN

    def test_late_delta_is_attributed_to_the_billing_month(self, billing_setup):
        access, payment_unit = billing_setup
        approved_work(access, payment_unit, approved=2, invoiced=1, approved_on=JAN_APPROVAL)

        (row,) = _build_billable_rows(access.opportunity, JAN, FEB_END)

        assert row.month == FEB

    @pytest.mark.parametrize("start_date, end_date", [(JAN, None), (None, FEB_END), (None, None)])
    def test_both_window_bounds_are_required(self, billing_setup, start_date, end_date):
        access, _ = billing_setup

        with pytest.raises(ValueError):
            _build_billable_rows(access.opportunity, start_date, end_date)


@pytest.mark.django_db
class TestLineItemGrouping:
    def test_groups_by_payment_unit_and_month(self, billing_setup):
        access, payment_unit = billing_setup
        other_unit = PaymentUnitFactory(opportunity=access.opportunity, amount=50, org_amount=0)
        approved_work(access, payment_unit)
        approved_work(access, payment_unit)
        approved_work(access, other_unit)
        approved_work(access, payment_unit, approved=2, invoiced=1)  # late delta -> February

        rows = _build_billable_rows(access.opportunity, JAN, FEB_END)
        items = group_line_items(rows, "USD")

        by_key = {(item["month"], item["payment_unit_name"]): item for item in items}
        assert len(by_key) == 3
        # 2 works for Jan and first payment unit
        january = by_key[(JAN, payment_unit.name)]
        assert january["number_approved"] == 2
        assert january["flw_amount_local"] == Decimal("200")
        assert january["org_amount_local"] == Decimal("40")
        assert january["total_amount_local"] == Decimal("240")
        assert january["currency"] == "USD"
        # 1 work for Jan and other payment unit
        assert by_key[(JAN, other_unit.name)]["total_amount_local"] == Decimal("50")
        # 1 delta for first payment unit, covered in february
        assert by_key[(FEB, payment_unit.name)]["number_approved"] == 1

    def test_same_named_payment_units_stay_separate_line_items(self, billing_setup):
        """Grouping is by payment unit id: `name` is not unique within an opportunity, and merging
        two units into one line item would understate the row count and hide one unit's rate."""
        access, payment_unit = billing_setup
        twin = PaymentUnitFactory(opportunity=access.opportunity, name=payment_unit.name, amount=7, org_amount=0)
        approved_work(access, payment_unit)
        approved_work(access, twin)

        items = group_line_items(_build_billable_rows(access.opportunity, JAN, JAN_END), "USD")

        assert [item["payment_unit_name"] for item in items] == [payment_unit.name, payment_unit.name]
        assert sorted(item["total_amount_local"] for item in items) == [Decimal("7"), Decimal("120")]

    def test_items_are_ordered_by_month(self, billing_setup):
        access, payment_unit = billing_setup
        approved_work(access, payment_unit, approved=2, invoiced=1)  # late delta -> February
        approved_work(access, payment_unit)  # January

        items = get_billable_line_items(access.opportunity, JAN, FEB_END)

        assert [item["month"] for item in items] == [JAN, FEB]
