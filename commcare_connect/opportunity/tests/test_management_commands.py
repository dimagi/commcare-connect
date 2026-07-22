import datetime

import pytest
from django.core.management import call_command

from commcare_connect.opportunity.models import (
    CompletedWorkInvoice,
    Currency,
    InvoiceStatus,
)
from commcare_connect.opportunity.tests.factories import (
    CompletedWorkFactory,
    ExchangeRateFactory,
    OpportunityAccessFactory,
    OpportunityFactory,
    PaymentInvoiceFactory,
    PaymentUnitFactory,
)


class ArchivePendingInvoicesTest:
    def test_archive_pending_invoices(self):
        fixed_cutoff_date = datetime.date(2025, 11, 1)

        opp_past = OpportunityFactory(end_date=fixed_cutoff_date - datetime.timedelta(days=1))  # 2025-10-31
        opp_on_cutoff = OpportunityFactory(end_date=fixed_cutoff_date)  # 2025-11-01
        opp_future = OpportunityFactory(end_date=fixed_cutoff_date + datetime.timedelta(days=1))  # 2025-11-02

        invoice1 = PaymentInvoiceFactory(opportunity=opp_past, status=InvoiceStatus.PENDING_NM_REVIEW)
        invoice2 = PaymentInvoiceFactory(opportunity=opp_on_cutoff, status=InvoiceStatus.PENDING_NM_REVIEW)
        invoice3 = PaymentInvoiceFactory(opportunity=opp_future, status=InvoiceStatus.PENDING_NM_REVIEW)
        invoice4 = PaymentInvoiceFactory(opportunity=opp_past, status=InvoiceStatus.PAID)
        invoice5 = PaymentInvoiceFactory(opportunity=opp_past, status=InvoiceStatus.PENDING_NM_REVIEW)

        call_command("archive_pending_invoices")

        invoice1.refresh_from_db()
        invoice2.refresh_from_db()
        invoice3.refresh_from_db()
        invoice4.refresh_from_db()
        invoice5.refresh_from_db()

        assert invoice1.status == InvoiceStatus.ARCHIVED
        assert invoice1.archived_date is not None

        assert invoice2.status == InvoiceStatus.ARCHIVED
        assert invoice2.archived_date is not None

        assert invoice3.status == InvoiceStatus.PENDING_NM_REVIEW
        assert invoice3.archived_date is None

        assert invoice4.status == InvoiceStatus.PAID
        assert invoice4.archived_date is None

        assert invoice5.status == InvoiceStatus.ARCHIVED
        assert invoice5.archived_date is not None


def _invoiced_work(opportunity, **saved):
    access = OpportunityAccessFactory(opportunity=opportunity)
    payment_unit = PaymentUnitFactory(opportunity=opportunity)
    invoice = PaymentInvoiceFactory(opportunity=opportunity, date=datetime.date(2026, 3, 15))
    defaults = dict(
        saved_approved_count=2,
        saved_payment_accrued=100,
        saved_payment_accrued_usd=1,
        saved_org_payment_accrued=40,
        saved_org_payment_accrued_usd="0.40",
        status="approved",
        status_modified_date=datetime.datetime(2026, 3, 10, tzinfo=datetime.UTC),
    )
    defaults.update(saved)
    work = CompletedWorkFactory(opportunity_access=access, payment_unit=payment_unit, invoice=invoice, **defaults)
    return invoice, work


@pytest.mark.django_db
class TestBackfillInvoiceLineItems:
    def test_creates_row_with_flw_and_org_amounts(self):
        # Non-USD opportunity so the ExchangeRate row is actually used
        # (USD short-circuits get_exchange_rate to 1, ignoring any rate row).
        opp = OpportunityFactory(currency=Currency.objects.get(code="KES"))
        ExchangeRateFactory(currency_code="KES", rate=50, rate_date=datetime.date(2026, 3, 1))
        invoice, work = _invoiced_work(opp)

        call_command("backfill_invoice_line_items")

        row = CompletedWorkInvoice.objects.get(invoice=invoice, completed_work=work)
        assert row.billed_count == 2
        assert row.flw_amount_local == 100
        assert row.flw_amount_usd == 1
        assert row.org_amount_local == 40
        assert float(row.org_amount_usd) == pytest.approx(0.40)
        assert row.month == datetime.date(2026, 3, 1)  # first of status_modified_date's month
        assert row.exchange_rate == 50  # monthly rate for a non-USD opportunity

    def test_sets_watermark_to_saved_approved_count(self):
        opp = OpportunityFactory()
        ExchangeRateFactory(currency_code=opp.currency_code, rate=50, rate_date=datetime.date(2026, 3, 1))
        _, work = _invoiced_work(opp, saved_approved_count=3)

        call_command("backfill_invoice_line_items")

        work.refresh_from_db()
        assert work.invoiced_approved_count == 3

    def test_usd_opportunity_stores_exchange_rate_of_one(self):
        opp = OpportunityFactory()  # USD by default in fixtures
        invoice, work = _invoiced_work(opp)

        call_command("backfill_invoice_line_items")

        row = CompletedWorkInvoice.objects.get(invoice=invoice, completed_work=work)
        assert row.exchange_rate == 1

    def test_skips_zero_approved_count_work(self):
        opp = OpportunityFactory()
        _, work = _invoiced_work(opp, saved_approved_count=0)

        call_command("backfill_invoice_line_items")

        assert not CompletedWorkInvoice.objects.filter(completed_work=work).exists()
        work.refresh_from_db()
        assert work.invoiced_approved_count == 0

    def test_is_idempotent(self):
        opp = OpportunityFactory()
        invoice, work = _invoiced_work(opp)

        call_command("backfill_invoice_line_items")
        call_command("backfill_invoice_line_items")

        assert CompletedWorkInvoice.objects.filter(invoice=invoice, completed_work=work).count() == 1

    def test_ignores_uninvoiced_work(self):
        opp = OpportunityFactory()
        access = OpportunityAccessFactory(opportunity=opp)
        CompletedWorkFactory(opportunity_access=access, invoice=None, saved_approved_count=2, status="approved")

        call_command("backfill_invoice_line_items")

        assert CompletedWorkInvoice.objects.count() == 0

    def test_rerun_after_more_approvals_does_not_desync_watermark(self):
        opp = OpportunityFactory()
        invoice, work = _invoiced_work(opp, saved_approved_count=2)
        call_command("backfill_invoice_line_items")
        work.refresh_from_db()
        row = CompletedWorkInvoice.objects.get(invoice=invoice, completed_work=work)
        assert work.invoiced_approved_count == 2
        assert row.billed_count == 2

        # More approvals land after the first backfill; the live count grows.
        work.saved_approved_count = 3
        work.save(update_fields=["saved_approved_count"])

        call_command("backfill_invoice_line_items")

        work.refresh_from_db()
        row.refresh_from_db()
        # Watermark and frozen row stay consistent — the extra unit is NOT retro-billed here.
        assert row.billed_count == 2
        assert work.invoiced_approved_count == 2
        assert CompletedWorkInvoice.objects.filter(completed_work=work).count() == 1

    def test_batch_size_processes_all_works_across_batches(self):
        opp = OpportunityFactory()
        _, w1 = _invoiced_work(opp)
        _, w2 = _invoiced_work(opp)

        call_command("backfill_invoice_line_items", "--batch-size", "1")

        assert CompletedWorkInvoice.objects.filter(completed_work__in=[w1, w2]).count() == 2
        for w in (w1, w2):
            w.refresh_from_db()
            assert w.invoiced_approved_count == w.saved_approved_count
