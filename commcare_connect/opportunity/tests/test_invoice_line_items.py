import datetime
from decimal import Decimal

import pytest
from django.db.models import Sum

from commcare_connect.opportunity.models import (
    CompletedWorkInvoice,
    CompletedWorkStatus,
    Currency,
    PaymentInvoice,
)
from commcare_connect.opportunity.tests.factories import (
    CompletedWorkFactory,
    ExchangeRateFactory,
    OpportunityAccessFactory,
    PaymentInvoiceFactory,
    PaymentUnitFactory,
)
from commcare_connect.opportunity.utils.invoice import get_start_date_for_invoice
from commcare_connect.opportunity.utils.invoice_line_items import (
    _build_billable_rows,
    create_invoice_line_items,
    get_billable_completed_works_qs,
    get_billable_delivery_rows,
    get_billable_line_items,
    get_invoice_delivery_rows,
    get_invoice_line_items,
    group_line_items,
    rollback_invoice_line_items,
)
from commcare_connect.utils.datetime import get_end_date_previous_month, get_month_start_date

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
        """An open-ended bound collapses to an empty Q() that matches every work — refuse it
        loudly rather than silently billing the whole opportunity."""
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


@pytest.mark.django_db
class TestCreateInvoiceLineItems:
    def _invoice(self, opportunity, start_date=JAN, end_date=FEB_END):
        return PaymentInvoiceFactory.build(
            opportunity=opportunity,
            service_delivery=True,
            amount=0,
            amount_usd=0,
            start_date=start_date,
            end_date=end_date,
        )

    def test_snapshots_one_row_per_work_with_flw_and_org_pay(self, billing_setup):
        access, payment_unit = billing_setup
        work = approved_work(access, payment_unit, approved=2)
        invoice = self._invoice(access.opportunity)

        create_invoice_line_items(invoice, start_date=JAN, end_date=FEB_END)

        row = CompletedWorkInvoice.objects.get(invoice=invoice, completed_work=work)
        assert row.billed_count == 2
        assert row.month == JAN
        assert row.flw_amount_local == Decimal("200")
        assert row.flw_amount_usd == Decimal("200")
        assert row.org_amount_local == Decimal("40")
        assert row.org_amount_usd == Decimal("40")
        assert row.exchange_rate == Decimal("1")

    def test_advances_the_watermark_to_the_saved_count(self, billing_setup):
        access, payment_unit = billing_setup
        work = approved_work(access, payment_unit, approved=3, invoiced=1)
        invoice = self._invoice(access.opportunity)

        create_invoice_line_items(invoice, start_date=JAN, end_date=FEB_END)

        work.refresh_from_db()
        assert work.invoiced_approved_count == 3
        assert CompletedWorkInvoice.objects.get(invoice=invoice).billed_count == 2

    def test_sets_invoice_totals_from_the_frozen_rows(self, billing_setup):
        access, payment_unit = billing_setup
        approved_work(access, payment_unit)
        approved_work(access, payment_unit)
        invoice = self._invoice(access.opportunity)

        create_invoice_line_items(invoice, start_date=JAN, end_date=FEB_END)

        invoice.refresh_from_db()
        assert invoice.amount == Decimal("240")
        assert invoice.amount_usd == Decimal("240")
        stored = invoice.work_items.aggregate(
            local=Sum("flw_amount_local") + Sum("org_amount_local"),
            usd=Sum("flw_amount_usd") + Sum("org_amount_usd"),
        )
        assert invoice.amount == stored["local"]
        assert invoice.amount_usd == stored["usd"]

    def test_usd_amounts_are_rounded_per_work_then_summed(self, billing_setup):
        """Ensures USD amounts are rounded per work item before summing.

        Without per-work rounding, this invoice would total 98.03 instead of 98.04 and
        would no longer match the sum of its line items.
        """
        access, payment_unit = billing_setup
        access.opportunity.currency = Currency.objects.get(code="KES")
        access.opportunity.save(update_fields=["currency"])
        ExchangeRateFactory(currency_code="KES", rate=Decimal("3.6725"), rate_date=datetime.date(2020, 1, 1))
        for _ in range(3):
            approved_work(access, payment_unit)
        invoice = self._invoice(access.opportunity)

        create_invoice_line_items(invoice, start_date=JAN, end_date=FEB_END)

        invoice.refresh_from_db()
        assert invoice.amount == Decimal("360")  # 3 x (100 + 20); local amounts never round
        # Each work is 100/3.6725 -> 27.23 plus 20/3.6725 -> 5.45, so 32.68 x 3.
        assert invoice.amount_usd == Decimal("98.04")
        assert invoice.work_items.count() == 3
        assert (
            invoice.amount_usd
            == invoice.work_items.aggregate(usd=Sum("flw_amount_usd") + Sum("org_amount_usd"))["usd"]
        )

    def test_leaves_the_billing_window_alone(self, billing_setup):
        """The window is the caller's input — an NM types it — so nothing here may narrow it to the
        months that happened to be billable."""
        access, payment_unit = billing_setup
        approved_work(access, payment_unit, approved_on=datetime.datetime(2026, 2, 3, tzinfo=datetime.UTC))
        invoice = self._invoice(access.opportunity, start_date=JAN, end_date=FEB_END)

        create_invoice_line_items(invoice, start_date=JAN, end_date=FEB_END)

        invoice.refresh_from_db()
        assert invoice.start_date == JAN
        assert invoice.end_date == FEB_END
        assert CompletedWorkInvoice.objects.get(invoice=invoice).month == FEB

    def test_a_late_delta_only_invoice_bills_under_the_billing_month(self, billing_setup):
        access, payment_unit = billing_setup
        work = approved_work(access, payment_unit, approved=2, invoiced=1, approved_on=JAN_APPROVAL)
        invoice = self._invoice(access.opportunity, start_date=FEB, end_date=FEB_END)

        create_invoice_line_items(invoice, start_date=FEB, end_date=FEB_END)

        row = CompletedWorkInvoice.objects.get(invoice=invoice, completed_work=work)
        assert row.billed_count == 1
        assert row.month == FEB

    def test_nothing_billable_writes_no_invoice_at_all(self, billing_setup):
        access, payment_unit = billing_setup
        approved_work(access, payment_unit, approved=1, invoiced=1)  # fully billed
        invoice = self._invoice(access.opportunity)

        assert create_invoice_line_items(invoice, start_date=JAN, end_date=FEB_END) == []

        assert invoice.pk is None
        assert not PaymentInvoice.objects.filter(opportunity=access.opportunity).exists()
        assert not CompletedWorkInvoice.objects.exists()

    def test_a_second_invoice_bills_only_the_new_delta(self, billing_setup):
        """The forward-delta acceptance case: a late approval bills on the next invoice, attributed
        to that invoice's month, and leaves the first invoice untouched."""
        access, payment_unit = billing_setup
        work = approved_work(access, payment_unit)
        january = self._invoice(access.opportunity, start_date=JAN, end_date=JAN_END)
        create_invoice_line_items(january, start_date=JAN, end_date=JAN_END)

        work.saved_approved_count = 2
        work.save(update_fields=["saved_approved_count"])
        february = self._invoice(access.opportunity, start_date=FEB, end_date=FEB_END)
        create_invoice_line_items(february, start_date=FEB, end_date=FEB_END)

        january.refresh_from_db()
        february.refresh_from_db()
        assert january.amount == Decimal("120")
        assert january.work_items.get().month == JAN
        assert february.amount == Decimal("120")
        assert february.work_items.get().month == FEB
        work.refresh_from_db()
        assert work.invoiced_approved_count == 2

    def test_a_skipped_month_still_bills_the_delta_on_the_next_invoice(self, billing_setup):
        """No invoice is issued in February at all. The delta must not be lost: March picks it up
        and attributes it to March, because the watermark — not any date — decides billability."""
        access, payment_unit = billing_setup
        work = approved_work(access, payment_unit)
        january = self._invoice(access.opportunity, start_date=JAN, end_date=JAN_END)
        create_invoice_line_items(january, start_date=JAN, end_date=JAN_END)

        work.saved_approved_count = 2
        work.save(update_fields=["saved_approved_count"])
        # February passes with no invoice created.

        march = self._invoice(access.opportunity, start_date=MAR, end_date=MAR_END)
        create_invoice_line_items(march, start_date=MAR, end_date=MAR_END)

        row = march.work_items.get()
        assert row.billed_count == 1
        assert row.month == MAR
        assert january.work_items.get().month == JAN  # January is untouched
        work.refresh_from_db()
        assert work.invoiced_approved_count == 2


@pytest.mark.django_db
class TestRollbackInvoiceLineItems:
    def _billed_invoice(self, access, start_date=JAN, end_date=FEB_END):
        invoice = PaymentInvoiceFactory(
            opportunity=access.opportunity,
            service_delivery=True,
            amount=0,
            amount_usd=0,
            start_date=start_date,
            end_date=end_date,
        )
        create_invoice_line_items(invoice, start_date=start_date, end_date=end_date)
        return invoice

    def test_deletes_the_rows_and_reopens_the_work(self, billing_setup):
        access, payment_unit = billing_setup
        work = approved_work(access, payment_unit, approved=2)
        invoice = self._billed_invoice(access)

        rollback_invoice_line_items(invoice)

        work.refresh_from_db()
        assert work.invoiced_approved_count == 0
        assert not invoice.work_items.exists()
        assert list(get_billable_completed_works_qs(access.opportunity, JAN, FEB_END)) == [work]

    def test_cancelling_one_of_two_covering_invoices_rolls_back_only_its_portion(self, billing_setup):
        access, payment_unit = billing_setup
        work = approved_work(access, payment_unit)
        january = self._billed_invoice(access, start_date=JAN, end_date=JAN_END)
        work.saved_approved_count = 3
        work.save(update_fields=["saved_approved_count"])
        february = self._billed_invoice(access, start_date=FEB, end_date=FEB_END)

        rollback_invoice_line_items(february)

        work.refresh_from_db()
        assert work.invoiced_approved_count == 1  # January's billing survives
        assert january.work_items.count() == 1
        assert not february.work_items.exists()

    def test_rollback_is_a_no_op_for_an_invoice_with_no_rows(self, billing_setup):
        access, _ = billing_setup
        invoice = PaymentInvoiceFactory(opportunity=access.opportunity, service_delivery=True, end_date=FEB_END)

        rollback_invoice_line_items(invoice)  # must not raise

        assert not invoice.work_items.exists()


@pytest.mark.django_db
class TestInvoicedLineItems:
    def test_reads_frozen_rows_grouped_by_payment_unit_and_month(self, billing_setup):
        access, payment_unit = billing_setup
        other_unit = PaymentUnitFactory(opportunity=access.opportunity, amount=50, org_amount=0)
        approved_work(access, payment_unit)
        approved_work(access, payment_unit)
        approved_work(access, other_unit)
        invoice = PaymentInvoiceFactory(
            opportunity=access.opportunity,
            service_delivery=True,
            amount=0,
            amount_usd=0,
            start_date=JAN,
            end_date=FEB_END,
        )
        create_invoice_line_items(invoice, start_date=JAN, end_date=FEB_END)

        items = get_invoice_line_items(invoice)

        by_name = {item["payment_unit_name"]: item for item in items}
        assert len(items) == 2
        assert by_name[payment_unit.name]["number_approved"] == 2
        assert by_name[payment_unit.name]["flw_amount_local"] == Decimal("200")
        assert by_name[payment_unit.name]["org_amount_local"] == Decimal("40")
        assert by_name[payment_unit.name]["total_amount_local"] == Decimal("240")
        assert by_name[payment_unit.name]["total_amount_usd"] == Decimal("240")
        assert by_name[payment_unit.name]["month"] == JAN
        assert by_name[payment_unit.name]["exchange_rate"] == Decimal("1")
        assert by_name[payment_unit.name]["currency"] == "USD"
        assert by_name[other_unit.name]["flw_amount_local"] == Decimal("50")
        assert by_name[other_unit.name]["org_amount_local"] == Decimal("0")
        assert by_name[other_unit.name]["total_amount_local"] == Decimal("50")
        assert by_name[other_unit.name]["total_amount_usd"] == Decimal("50")

    def test_a_later_approval_does_not_change_an_issued_invoice(self, billing_setup):
        """The drift acceptance case: recomputing saved_* after issue must not move the line items."""
        access, payment_unit = billing_setup
        work = approved_work(access, payment_unit)
        invoice = PaymentInvoiceFactory(
            opportunity=access.opportunity,
            service_delivery=True,
            amount=0,
            amount_usd=0,
            start_date=JAN,
            end_date=JAN_END,
        )
        create_invoice_line_items(invoice, start_date=JAN, end_date=JAN_END)

        work.saved_approved_count = 5
        work.saved_payment_accrued = 500
        work.save(update_fields=["saved_approved_count", "saved_payment_accrued"])

        (item,) = get_invoice_line_items(invoice)
        assert item["number_approved"] == 1
        assert item["total_amount_local"] == Decimal("120")

    def test_no_rows_yields_no_items(self, billing_setup):
        access, _ = billing_setup
        invoice = PaymentInvoiceFactory(opportunity=access.opportunity, service_delivery=True, end_date=FEB_END)

        assert get_invoice_line_items(invoice) == []


@pytest.mark.django_db
class TestStartDateForInvoice:
    def test_uses_the_earliest_first_billing_approval_month(self, billing_setup):
        access, payment_unit = billing_setup
        approved_work(access, payment_unit, approved_on=datetime.datetime(2026, 2, 20, tzinfo=datetime.UTC))
        approved_work(access, payment_unit, approved_on=datetime.datetime(2026, 4, 2, tzinfo=datetime.UTC))

        assert get_start_date_for_invoice(access.opportunity) == FEB

    def test_a_late_delta_does_not_drag_the_start_date_back(self, billing_setup):
        """A late delta's status_modified_date is frozen at the original (already billed) approval."""
        access, payment_unit = billing_setup
        approved_work(access, payment_unit, approved=2, invoiced=1, approved_on=JAN_APPROVAL)
        approved_work(access, payment_unit, approved_on=datetime.datetime(2026, 4, 2, tzinfo=datetime.UTC))

        assert get_start_date_for_invoice(access.opportunity) == datetime.date(2026, 4, 1)

    def test_falls_back_to_the_billing_month_when_only_late_deltas_remain(self, billing_setup):
        """Nothing awaits first billing, so anything still billable is a late delta — and a late
        delta bills under the invoice's own month."""
        access, payment_unit = billing_setup
        access.opportunity.start_date = datetime.date(2025, 9, 14)
        access.opportunity.save(update_fields=["start_date"])
        approved_work(access, payment_unit, approved=2, invoiced=1, approved_on=JAN_APPROVAL)

        start_date = get_start_date_for_invoice(access.opportunity)

        # Asserted as relationships so the test doesn't hardcode "now".
        end_date = get_end_date_previous_month()
        # For only late delta, it is start of billing month which defaults to last month.
        assert start_date <= end_date
        assert start_date == get_month_start_date(end_date)

    def test_falls_back_to_the_opportunity_start_when_nothing_is_billable(self, billing_setup):
        """No invoice can come of this window at all, so today's behaviour is kept."""
        access, payment_unit = billing_setup
        access.opportunity.start_date = datetime.date(2025, 9, 14)
        access.opportunity.save(update_fields=["start_date"])
        approved_work(access, payment_unit, approved=1, invoiced=1)  # fully billed: not billable

        assert get_start_date_for_invoice(access.opportunity) == datetime.date(2025, 9, 1)


@pytest.mark.django_db
class TestDeliveryRows:
    def test_billable_rows_carry_the_delta_and_delivery_identity(self, billing_setup):
        access, payment_unit = billing_setup
        work = approved_work(access, payment_unit, approved=3, invoiced=1)

        (row,) = get_billable_delivery_rows(access.opportunity, JAN, FEB_END)

        assert row["payment_unit"] == payment_unit.name
        assert row["opportunity"] == access.opportunity.name
        assert row["entity_name"] == work.entity_name
        assert row["username"] == access.user.name
        assert row["approved_count"] == 2
        assert row["flw_amount_local"] == Decimal("200")
        assert row["org_amount_local"] == Decimal("40")
        assert row["total_amount_local"] == Decimal("240")
        assert row["total_amount_usd"] == Decimal("240")

    def test_invoice_rows_stay_frozen_after_a_later_approval(self, billing_setup):
        access, payment_unit = billing_setup
        work = approved_work(access, payment_unit)
        invoice = PaymentInvoiceFactory(
            opportunity=access.opportunity,
            service_delivery=True,
            amount=0,
            amount_usd=0,
            start_date=JAN,
            end_date=JAN_END,
        )
        create_invoice_line_items(invoice, start_date=JAN, end_date=JAN_END)

        work.saved_approved_count = 9
        work.save(update_fields=["saved_approved_count"])

        (row,) = get_invoice_delivery_rows(invoice)
        assert row["approved_count"] == 1
        assert row["total_amount_local"] == Decimal("120")
