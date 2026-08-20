from datetime import date, datetime, timezone
from decimal import ROUND_HALF_EVEN, Decimal

import pytest
from dateutil.relativedelta import relativedelta
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
    CENTS,
    Money,
    _build_billable_rows,
    bill_invoice,
    get_billable_completed_works_qs,
    get_billable_delivery_rows_for_export,
    get_billable_line_items,
    get_invoice_delivery_rows_for_export,
    get_invoice_line_items,
    group_line_items,
)
from commcare_connect.utils.datetime import get_end_date_previous_month, get_month_start_date

JAN = date(2026, 1, 1)
JAN_END = date(2026, 1, 31)
FEB = date(2026, 2, 1)
FEB_END = date(2026, 2, 28)
MAR = date(2026, 3, 1)
MAR_END = date(2026, 3, 31)
JAN_APPROVAL = datetime(2026, 1, 15, tzinfo=timezone.utc)
FEB_APPROVAL = datetime(2026, 2, 20, tzinfo=timezone.utc)
APR_APPROVAL = datetime(2026, 4, 2, tzinfo=timezone.utc)
APR_END = date(2026, 4, 30)


@pytest.fixture
def billing_setup(db):
    """A USD opportunity (exchange rate 1) with a payment unit worth 100 FLW pay + 20 org pay."""
    access = OpportunityAccessFactory()
    payment_unit = PaymentUnitFactory(opportunity=access.opportunity, amount=100, org_amount=20)
    ExchangeRateFactory(currency_code="USD", rate=1, rate_date=date(2020, 1, 1))
    return access, payment_unit


def completed_work(
    access,
    payment_unit,
    *,
    approved=1,
    invoiced=0,
    approved_on=JAN_APPROVAL,
    status=CompletedWorkStatus.approved,
):
    return CompletedWorkFactory(
        opportunity_access=access,
        payment_unit=payment_unit,
        status=status,
        saved_approved_count=approved,
        invoiced_approved_count=invoiced,
        status_modified_date=approved_on,
    )


class TestMoney:
    @pytest.mark.parametrize(
        "local, rate, expected_usd",
        [
            pytest.param("100", "1", "100.00", id="parity"),
            pytest.param("100", "3.6725", "27.23", id="rounds-to-cents"),
            # Both land exactly on a half cent; ROUND_HALF_EVEN sends them to the even neighbour,
            # which is what Django's DecimalField does when it stores the result.
            pytest.param("0.125", "1", "0.12", id="half-rounds-down-to-even"),
            pytest.param("0.135", "1", "0.14", id="half-rounds-up-to-even"),
        ],
    )
    def test_from_local_amount_prices_and_quantizes(self, local, rate, expected_usd):
        money = Money.from_local_amount(Decimal(local), Decimal(rate))

        assert money.local == Decimal(local)  # local amounts are never rounded
        assert money.usd == Decimal(expected_usd)

    def test_addition_keeps_the_two_amounts_in_step(self):
        assert Money(Decimal("100"), Decimal("27.23")) + Money(Decimal("20"), Decimal("5.45")) == Money(
            Decimal("120"), Decimal("32.68")
        )

    def test_sum_starts_from_its_implicit_zero(self):
        amounts = [Money.from_local_amount(Decimal("100"), Decimal("3.6725")) for _ in range(3)]

        assert sum(amounts) == Money(Decimal("300"), Decimal("81.69"))
        assert sum([], Money.zero()) == Money.zero()

    def test_adding_a_bare_number_is_refused(self):
        with pytest.raises(TypeError):
            Money.zero() + Decimal("10")


def billable_rows(opportunity, start_date, end_date):
    works = get_billable_completed_works_qs(opportunity, start_date, end_date)
    return _build_billable_rows(works, opportunity.currency_code, end_date)


@pytest.mark.django_db
class TestBillableSelection:
    APPROVED = CompletedWorkStatus.approved

    @pytest.mark.parametrize(
        "approved, invoiced, status, approved_on, window, billable",
        [
            pytest.param(1, 0, APPROVED, JAN_APPROVAL, (JAN, JAN_END), True, id="first-billing-in-window"),
            pytest.param(1, 0, APPROVED, JAN_APPROVAL, (FEB, FEB_END), False, id="first-billing-out-of-window"),
            # A late duplicate keeps status_modified_date at the original (January) approval, so the
            # window's start can't sensibly apply -- it must bill on the next invoice regardless.
            pytest.param(2, 1, APPROVED, JAN_APPROVAL, (FEB, FEB_END), True, id="late-delta-bypasses-window-start"),
            # The window's end does apply: a back-dated invoice must not bill an approval that only
            # landed in April. It bills on the next window that ends after it.
            pytest.param(2, 1, APPROVED, APR_APPROVAL, (FEB, FEB_END), False, id="late-delta-after-window-end"),
            pytest.param(2, 1, APPROVED, APR_APPROVAL, (FEB, APR_END), True, id="late-delta-inside-window-end"),
            pytest.param(2, 2, APPROVED, JAN_APPROVAL, (JAN, FEB_END), False, id="fully-billed"),
            pytest.param(1, 0, CompletedWorkStatus.pending, JAN_APPROVAL, (JAN, FEB_END), False, id="not-approved"),
        ],
    )
    def test_billability(self, billing_setup, approved, invoiced, status, approved_on, window, billable):
        access, payment_unit = billing_setup
        work = completed_work(
            access, payment_unit, approved=approved, invoiced=invoiced, status=status, approved_on=approved_on
        )

        qs = get_billable_completed_works_qs(access.opportunity, *window)

        assert list(qs) == ([work] if billable else [])


@pytest.mark.django_db
class TestBillableRows:
    def test_prices_only_the_unbilled_delta_including_org_pay(self, billing_setup):
        access, payment_unit = billing_setup
        completed_work(access, payment_unit, approved=3, invoiced=1)

        (row,) = billable_rows(access.opportunity, JAN, FEB_END)

        assert row.billed_count == 2
        assert row.flw_pay.local == Decimal("200")
        assert row.org_pay.local == Decimal("40")
        assert row.total_pay.local == Decimal("240")
        assert row.exchange_rate.rate == Decimal("1")
        assert row.total_pay.usd == Decimal("240")

    @pytest.mark.parametrize(
        "approved, invoiced, end_date, expected_month",
        [
            pytest.param(1, 0, FEB_END, JAN, id="first-billing-takes-its-approval-month"),
            # A late delta's status_modified_date is frozen at the January approval, so it can only
            # be billed under the month the invoice covers.
            pytest.param(2, 1, FEB_END, FEB, id="late-delta-takes-the-billing-month"),
            # An NM types the window by hand, so the end date need not be a month end. The delta
            # takes the month it falls in, truncated to the 1st.
            pytest.param(2, 1, date(2026, 3, 15), MAR, id="late-delta-takes-a-mid-month-end-date"),
        ],
    )
    def test_attributes_the_delta_to_a_month(self, billing_setup, approved, invoiced, end_date, expected_month):
        access, payment_unit = billing_setup
        completed_work(access, payment_unit, approved=approved, invoiced=invoiced, approved_on=JAN_APPROVAL)

        (row,) = billable_rows(access.opportunity, JAN, end_date)

        assert row.month == expected_month

    @pytest.mark.parametrize("start_date, end_date", [(JAN, None), (None, FEB_END), (None, None)])
    def test_both_window_bounds_are_required(self, billing_setup, start_date, end_date):
        access, _ = billing_setup

        with pytest.raises(ValueError):
            billable_rows(access.opportunity, start_date, end_date)


@pytest.mark.django_db
class TestLineItemGrouping:
    def test_groups_by_payment_unit_and_month_in_month_order(self, billing_setup):
        access, payment_unit = billing_setup
        other_unit = PaymentUnitFactory(opportunity=access.opportunity, amount=50, org_amount=0)
        completed_work(access, payment_unit)
        completed_work(access, payment_unit)
        completed_work(access, other_unit)
        completed_work(access, payment_unit, approved=2, invoiced=1)  # late delta -> February

        items = group_line_items(billable_rows(access.opportunity, JAN, FEB_END))

        assert [item.month for item in items] == [JAN, JAN, FEB]
        by_key = {(item.month, item.payment_unit_name): item for item in items}
        assert len(by_key) == 3
        # 2 works for Jan and first payment unit
        january = by_key[(JAN, payment_unit.name)]
        assert january.number_approved == 2
        assert january.flw_pay.local == Decimal("200")
        assert january.org_pay.local == Decimal("40")
        assert january.total_pay.local == Decimal("240")
        assert january.exchange_rate == Decimal("1")
        # 1 work for Jan and other payment unit
        assert by_key[(JAN, other_unit.name)].total_pay.local == Decimal("50")
        # 1 delta for first payment unit, covered in february
        assert by_key[(FEB, payment_unit.name)].number_approved == 1

    def test_same_named_payment_units_stay_separate_line_items(self, billing_setup):
        """Grouping is by payment unit id: `name` is not unique within an opportunity, and merging
        two units into one line item would understate the row count and hide one unit's rate."""
        access, payment_unit = billing_setup
        twin = PaymentUnitFactory(opportunity=access.opportunity, name=payment_unit.name, amount=7, org_amount=0)
        completed_work(access, payment_unit)
        completed_work(access, twin)

        items = group_line_items(billable_rows(access.opportunity, JAN, JAN_END))

        assert [item.payment_unit_name for item in items] == [payment_unit.name, payment_unit.name]
        assert sorted(item.total_pay.local for item in items) == [Decimal("7"), Decimal("120")]


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

    @pytest.mark.parametrize(
        "approved, invoiced, expected_month",
        [
            pytest.param(2, 0, JAN, id="first-billing"),
            pytest.param(3, 1, FEB, id="late-delta"),
        ],
    )
    def test_snapshots_the_delta_and_advances_the_invoiced_count(
        self, billing_setup, approved, invoiced, expected_month
    ):
        access, payment_unit = billing_setup
        work = completed_work(access, payment_unit, approved=approved, invoiced=invoiced)
        invoice = self._invoice(access.opportunity)

        bill_invoice(invoice, start_date=JAN, end_date=FEB_END)

        row = CompletedWorkInvoice.objects.get(invoice=invoice, completed_work=work)
        assert row.billed_count == 2  # both cases have two unbilled units
        assert row.month == expected_month
        assert row.flw_amount_local == Decimal("200")
        assert row.flw_amount_usd == Decimal("200")
        assert row.org_amount_local == Decimal("40")
        assert row.org_amount_usd == Decimal("40")
        assert row.exchange_rate.rate == Decimal("1")
        work.refresh_from_db()
        assert work.invoiced_approved_count == approved

    @pytest.mark.parametrize(
        "currency_code, rate, work_count, expected_local, expected_usd",
        [
            pytest.param("USD", Decimal("1"), 2, Decimal("240"), Decimal("240"), id="usd"),
            # Each work is 100/3.6725 -> 27.23 plus 20/3.6725 -> 5.45, so 32.68 x 3. Without
            # per-work rounding this invoice would total 98.03 and stop matching its line items.
            pytest.param("KES", Decimal("3.6725"), 3, Decimal("360"), Decimal("98.04"), id="kes-rounds-per-work"),
        ],
    )
    def test_invoice_totals_equal_the_sum_of_the_frozen_rows(
        self, billing_setup, currency_code, rate, work_count, expected_local, expected_usd
    ):
        """USD amounts are rounded per work before summing, so an invoice total can never drift from
        the line items stored against it."""
        access, payment_unit = billing_setup
        if currency_code != "USD":
            access.opportunity.currency = Currency.objects.get(code=currency_code)
            access.opportunity.save(update_fields=["currency"])
            ExchangeRateFactory(currency_code=currency_code, rate=rate, rate_date=date(2020, 1, 1))
        for _ in range(work_count):
            completed_work(access, payment_unit)
        invoice = self._invoice(access.opportunity)

        bill_invoice(invoice, start_date=JAN, end_date=FEB_END)

        invoice.refresh_from_db()
        assert invoice.amount == expected_local  # local amounts never round
        assert invoice.amount_usd == expected_usd
        assert invoice.work_items.count() == work_count
        stored = invoice.work_items.aggregate(
            local=Sum("flw_amount_local") + Sum("org_amount_local"),
            usd=Sum("flw_amount_usd") + Sum("org_amount_usd"),
        )
        assert invoice.amount == stored["local"]
        assert invoice.amount_usd == stored["usd"]

    def test_leaves_the_billing_window_alone(self, billing_setup):
        """The window is the caller's input — an NM types it — so nothing here may narrow it to the
        months that happened to be billable."""
        access, payment_unit = billing_setup
        completed_work(access, payment_unit, approved_on=FEB_APPROVAL)
        invoice = self._invoice(access.opportunity, start_date=JAN, end_date=FEB_END)

        bill_invoice(invoice, start_date=JAN, end_date=FEB_END)

        invoice.refresh_from_db()
        assert invoice.start_date == JAN
        assert invoice.end_date == FEB_END
        assert CompletedWorkInvoice.objects.get(invoice=invoice).month == FEB

    def test_nothing_billable_writes_no_invoice_at_all(self, billing_setup):
        access, payment_unit = billing_setup
        completed_work(access, payment_unit, approved=1, invoiced=1)  # fully billed
        invoice = self._invoice(access.opportunity)

        assert bill_invoice(invoice, start_date=JAN, end_date=FEB_END) == []

        assert invoice.pk is None
        assert not PaymentInvoice.objects.filter(opportunity=access.opportunity).exists()
        assert not CompletedWorkInvoice.objects.exists()

    @pytest.mark.parametrize(
        "second_window, expected_month",
        [
            pytest.param((FEB, FEB_END), FEB, id="on-the-next-invoice"),
            # February passes with no invoice at all: the delta must not be lost, so March picks
            # it up and bills it under March.
            pytest.param((MAR, MAR_END), MAR, id="after-a-skipped-month"),
        ],
    )
    def test_a_later_invoice_bills_only_the_new_delta(self, billing_setup, second_window, expected_month):
        """The forward-delta acceptance case: a late approval bills on the next invoice issued,
        attributed to that invoice's month, and leaves the first invoice untouched."""
        access, payment_unit = billing_setup
        work = completed_work(access, payment_unit)
        january = self._invoice(access.opportunity, start_date=JAN, end_date=JAN_END)
        bill_invoice(january, start_date=JAN, end_date=JAN_END)

        work.saved_approved_count = 2
        work.save(update_fields=["saved_approved_count"])

        start_date, end_date = second_window
        later = self._invoice(access.opportunity, start_date=start_date, end_date=end_date)
        bill_invoice(later, start_date=start_date, end_date=end_date)

        january.refresh_from_db()
        later.refresh_from_db()
        assert january.amount == Decimal("120")
        assert january.work_items.get().month == JAN  # the first invoice is untouched
        assert later.amount == Decimal("120")
        row = later.work_items.get()
        assert row.billed_count == 1
        assert row.month == expected_month
        work.refresh_from_db()
        assert work.invoiced_approved_count == 2


@pytest.mark.django_db
class TestInvoicedLineItems:
    def test_reads_frozen_rows_grouped_by_payment_unit_and_month(self, billing_setup):
        access, payment_unit = billing_setup
        other_unit = PaymentUnitFactory(opportunity=access.opportunity, amount=50, org_amount=0)
        completed_work(access, payment_unit)
        completed_work(access, payment_unit)
        completed_work(access, other_unit)
        invoice = PaymentInvoiceFactory(
            opportunity=access.opportunity,
            service_delivery=True,
            amount=0,
            amount_usd=0,
            start_date=JAN,
            end_date=FEB_END,
        )
        bill_invoice(invoice, start_date=JAN, end_date=FEB_END)

        items = get_invoice_line_items(invoice)

        by_name = {item.payment_unit_name: item for item in items}
        assert len(items) == 2
        assert by_name[payment_unit.name].number_approved == 2
        assert by_name[payment_unit.name].flw_pay.local == Decimal("200")
        assert by_name[payment_unit.name].org_pay.local == Decimal("40")
        assert by_name[payment_unit.name].total_pay.local == Decimal("240")
        assert by_name[payment_unit.name].total_pay.usd == Decimal("240")
        assert by_name[payment_unit.name].month == JAN
        assert by_name[payment_unit.name].exchange_rate == Decimal("1")
        assert by_name[other_unit.name].flw_pay.local == Decimal("50")
        assert by_name[other_unit.name].org_pay.local == Decimal("0")
        assert by_name[other_unit.name].total_pay.local == Decimal("50")
        assert by_name[other_unit.name].total_pay.usd == Decimal("50")

    def test_a_later_approval_does_not_change_an_issued_invoice(self, billing_setup):
        """The drift acceptance case: recomputing saved_* after issue must not move the line items."""
        access, payment_unit = billing_setup
        work = completed_work(access, payment_unit)
        invoice = PaymentInvoiceFactory(
            opportunity=access.opportunity,
            service_delivery=True,
            amount=0,
            amount_usd=0,
            start_date=JAN,
            end_date=JAN_END,
        )
        bill_invoice(invoice, start_date=JAN, end_date=JAN_END)

        work.saved_approved_count = 5
        work.saved_payment_accrued = 500
        work.save(update_fields=["saved_approved_count", "saved_payment_accrued"])

        (item,) = get_invoice_line_items(invoice)
        assert item.number_approved == 1
        assert item.total_pay.local == Decimal("120")

    def test_no_rows_yields_no_items(self, billing_setup):
        access, _ = billing_setup
        invoice = PaymentInvoiceFactory(opportunity=access.opportunity, service_delivery=True, end_date=FEB_END)

        assert get_invoice_line_items(invoice) == []


@pytest.mark.django_db
class TestWorkPayRowReaders:
    def _invoice(self, opportunity, start_date=JAN, end_date=FEB_END):
        return PaymentInvoiceFactory.build(
            opportunity=opportunity,
            service_delivery=True,
            amount=0,
            amount_usd=0,
            start_date=start_date,
            end_date=end_date,
        )

    def test_reads_the_frozen_delta_and_the_delivery_it_came_from(self, billing_setup):
        access, payment_unit = billing_setup
        work = completed_work(access, payment_unit, approved=3, invoiced=1)

        invoice = self._invoice(access.opportunity)
        bill_invoice(invoice, start_date=JAN, end_date=FEB_END)
        (row,) = get_invoice_delivery_rows_for_export(invoice)

        assert row.completed_work == work
        assert row.billed_count == 2  # the unbilled delta, not saved_approved_count
        assert row.month == FEB  # a late delta bills under the invoice's month
        assert row.flw_pay.local == Decimal("200")
        assert row.org_pay.local == Decimal("40")
        assert row.total_pay.local == Decimal("240")
        assert row.total_pay.usd == Decimal("240")
        assert row.exchange_rate.rate == Decimal("1")

    def test_issued_rows_stay_frozen_while_billable_rows_move_on(self, billing_setup):
        """Why there are two readers: an issued invoice's export shows what was billed, while the
        preview shows what is still owed."""
        access, payment_unit = billing_setup
        work = completed_work(access, payment_unit, status=CompletedWorkStatus.approved, approved=1, invoiced=0)
        invoice = self._invoice(access.opportunity, start_date=JAN, end_date=JAN_END)
        bill_invoice(invoice, start_date=JAN, end_date=JAN_END)

        work.saved_approved_count = 3
        work.save(update_fields=["saved_approved_count"])

        (frozen,) = get_invoice_delivery_rows_for_export(invoice)
        (billable,) = get_billable_delivery_rows_for_export(access.opportunity, FEB, FEB_END)

        assert frozen.billed_count == 1
        assert frozen.total_pay.local == Decimal("120")
        assert billable.billed_count == 2
        assert billable.total_pay.local == Decimal("240")

    def test_a_month_mixes_first_billings_and_deltas(self, billing_setup):
        access, payment_unit = billing_setup
        late = completed_work(access, payment_unit, approved_on=JAN_APPROVAL, approved=1, invoiced=0)
        january = self._invoice(access.opportunity, start_date=JAN, end_date=JAN_END)
        bill_invoice(january, start_date=JAN, end_date=JAN_END)
        late.saved_approved_count = 2
        late.save(update_fields=["saved_approved_count"])
        fresh = completed_work(access, payment_unit, approved_on=FEB_APPROVAL)

        february = self._invoice(access.opportunity, start_date=FEB, end_date=FEB_END)
        bill_invoice(february, start_date=FEB, end_date=FEB_END)

        rows = {row.completed_work: row for row in get_invoice_delivery_rows_for_export(february)}

        assert {row.month for row in rows.values()} == {FEB}
        assert rows[late].billed_count == 1
        assert rows[fresh].billed_count == 1


@pytest.mark.django_db
class TestStartDateForInvoice:
    @pytest.mark.parametrize(
        "works, opportunity_start, expected",
        [
            pytest.param(
                [(1, 0, FEB_APPROVAL), (1, 0, APR_APPROVAL)],
                None,
                FEB,
                id="earliest-first-billing-approval-month",
            ),
            # A late delta's status_modified_date is frozen at the original (already billed)
            # approval, so it must not drag the start date back to January.
            pytest.param(
                [(2, 1, JAN_APPROVAL), (1, 0, APR_APPROVAL)],
                None,
                date(2026, 4, 1),
                id="late-delta-does-not-drag-it-back",
            ),
            # No invoice can come of this window at all, so today's behaviour is kept.
            pytest.param(
                [(1, 1, JAN_APPROVAL)],
                date(2025, 9, 14),
                date(2025, 9, 1),
                id="nothing-billable-falls-back-to-the-opportunity-start",
            ),
        ],
    )
    def test_start_date(self, billing_setup, works, opportunity_start, expected):
        access, payment_unit = billing_setup
        if opportunity_start:
            access.opportunity.start_date = opportunity_start
            access.opportunity.save(update_fields=["start_date"])
        for approved, invoiced, approved_on in works:
            completed_work(access, payment_unit, approved=approved, invoiced=invoiced, approved_on=approved_on)

        assert get_start_date_for_invoice(access.opportunity) == expected

    def test_falls_back_to_the_billing_month_when_only_late_deltas_remain(self, billing_setup):
        """Nothing awaits first billing, so anything still billable is a late delta — and a late
        delta bills under the invoice's own month."""
        access, payment_unit = billing_setup
        access.opportunity.start_date = date(2025, 9, 14)
        access.opportunity.save(update_fields=["start_date"])
        completed_work(access, payment_unit, approved=2, invoiced=1, approved_on=JAN_APPROVAL)

        start_date = get_start_date_for_invoice(access.opportunity)

        # Asserted as relationships so the test doesn't hardcode "now".
        end_date = get_end_date_previous_month()
        # For only late delta, it is start of billing month which defaults to last month.
        assert start_date <= end_date
        assert start_date == get_month_start_date(end_date)


@pytest.mark.django_db
class TestBillableLineItemsAcrossMonths:
    @pytest.fixture
    def access(self):
        """A USD opportunity with a baseline rate of 1; each test adds its own payment units."""
        opp_access = OpportunityAccessFactory()
        ExchangeRateFactory(
            currency_code=opp_access.opportunity.currency_code, rate=Decimal("1"), rate_date=date(2020, 1, 1)
        )
        return opp_access

    def test_groups_each_month_and_unit_at_the_rate_in_force(self, access):
        two_months_ago, one_month_ago, now = self._recent_months()
        rates = self._pin_monthly_rates(
            access.opportunity, [(two_months_ago, "0.25"), (one_month_ago, "0.50"), (now, "0.75")]
        )
        unit_a = PaymentUnitFactory(opportunity=access.opportunity, amount=100, org_amount=0)
        unit_b = PaymentUnitFactory(opportunity=access.opportunity, amount=50, org_amount=0)
        # One entry per expected line item; the list is each of its works' saved_approved_count.
        groups = [
            (two_months_ago, unit_a, [1, 1]),
            (two_months_ago, unit_b, [1]),
            (one_month_ago, unit_a, [2]),
            (now, unit_a, [1]),
            (now, unit_b, [1]),
        ]
        for approved_on, payment_unit, approvals in groups:
            for approved in approvals:
                self._create_approved_completed_work(access, approved_on, payment_unit, approved=approved)

        items = self._billable_items(access.opportunity)

        assert len(items) == len(groups)
        by_key = {(item.month.month, item.payment_unit_name): item for item in items}
        for approved_on, payment_unit, approvals in groups:
            item = by_key[(approved_on.month, payment_unit.name)]
            self._assert_priced(item, sum(approvals), payment_unit, rates[approved_on.month])

    def test_total_includes_org_pay(self, access):
        payment_unit = PaymentUnitFactory(opportunity=access.opportunity, amount=10, org_amount=4)
        now = datetime.now(tz=timezone.utc)
        self._pin_monthly_rates(access.opportunity, [(now, "2")])
        self._create_approved_completed_work(access, now, payment_unit, approved=2)

        (item,) = self._billable_items(access.opportunity)
        # Raw FLW/Org breakdowns are surfaced separately.
        assert item.flw_pay.local == Decimal("20")
        assert item.org_pay.local == Decimal("8")
        assert item.flw_pay.usd == Decimal("10")
        assert item.org_pay.usd == Decimal("4")
        # Totals always fold in org pay (FLW + Org).
        assert item.total_pay.local == Decimal("28")
        assert item.total_pay.usd == Decimal("14")

    def _billable_items(self, opportunity):
        """These tests span three months back, so bill from before the earliest through today."""
        start_date = (datetime.now(tz=timezone.utc) - relativedelta(months=4)).date()
        return get_billable_line_items(opportunity, start_date, datetime.now(tz=timezone.utc).date())

    def _create_approved_completed_work(self, opp_access, status_modified_date, payment_unit, approved=1):
        return CompletedWorkFactory(
            status=CompletedWorkStatus.approved,
            opportunity_access=opp_access,
            payment_unit=payment_unit,
            saved_approved_count=approved,
            invoiced_approved_count=0,
            status_modified_date=status_modified_date,
        )

    def _recent_months(self):
        now = datetime.now(tz=timezone.utc)
        return now - relativedelta(months=2), now - relativedelta(months=1), now

    def _pin_monthly_rates(self, opportunity, rates):
        return {
            approved_on.month: ExchangeRateFactory(
                currency_code=opportunity.currency_code,
                rate=Decimal(rate),
                rate_date=get_month_start_date(approved_on),
            ).rate
            for approved_on, rate in rates
        }

    def _assert_priced(self, item, expected_count, payment_unit, expected_rate):
        total_local = expected_count * payment_unit.amount

        assert item.number_approved == expected_count
        assert item.total_pay.local == total_local
        assert item.exchange_rate == expected_rate
        assert item.total_pay.usd == (total_local / expected_rate).quantize(CENTS, rounding=ROUND_HALF_EVEN)
