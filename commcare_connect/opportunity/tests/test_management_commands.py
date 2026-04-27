import datetime
from decimal import Decimal

import pytest
from django.core.management import call_command
from django.utils.timezone import now

from commcare_connect.opportunity.models import CompletedWorkStatus, InvoiceStatus, PaymentInvoiceLineItem
from commcare_connect.opportunity.tests.factories import (
    CompletedWorkFactory,
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


@pytest.mark.django_db
class TestBackfillInvoiceLineItems:
    def _invoice_with_linked_cw(self, opportunity):
        """Helper: create service-delivery invoice + linked approved CW."""
        pu = PaymentUnitFactory(opportunity=opportunity)
        opp_access = OpportunityAccessFactory(opportunity=opportunity)
        invoice = PaymentInvoiceFactory(opportunity=opportunity, service_delivery=True)
        CompletedWorkFactory(
            opportunity_access=opp_access,
            payment_unit=pu,
            status=CompletedWorkStatus.approved,
            saved_payment_accrued=100,
            saved_payment_accrued_usd=Decimal("1.00"),
            status_modified_date=now(),
            invoice=invoice,
        )
        return invoice

    def test_creates_line_items_for_un_backfilled_invoice(self, opportunity):
        invoice = self._invoice_with_linked_cw(opportunity)
        assert PaymentInvoiceLineItem.objects.filter(invoice=invoice).count() == 0
        call_command("backfill_invoice_line_items")
        assert PaymentInvoiceLineItem.objects.filter(invoice=invoice).count() == 1

    def test_idempotent(self, opportunity):
        invoice = self._invoice_with_linked_cw(opportunity)
        call_command("backfill_invoice_line_items")
        first_count = PaymentInvoiceLineItem.objects.filter(invoice=invoice).count()
        call_command("backfill_invoice_line_items")
        assert PaymentInvoiceLineItem.objects.filter(invoice=invoice).count() == first_count

    def test_dry_run_writes_nothing(self, opportunity):
        self._invoice_with_linked_cw(opportunity)
        call_command("backfill_invoice_line_items", "--dry-run")
        assert PaymentInvoiceLineItem.objects.count() == 0

    def test_skips_custom_invoices(self, opportunity):
        custom = PaymentInvoiceFactory(opportunity=opportunity, service_delivery=False)
        call_command("backfill_invoice_line_items")
        assert PaymentInvoiceLineItem.objects.filter(invoice=custom).count() == 0

    def test_opportunity_id_filter(self, opportunity, organization):
        other_opp = OpportunityFactory(organization=organization)
        target_invoice = self._invoice_with_linked_cw(opportunity)
        other_invoice = self._invoice_with_linked_cw(other_opp)

        call_command("backfill_invoice_line_items", "--opportunity-id", str(opportunity.id))

        assert PaymentInvoiceLineItem.objects.filter(invoice=target_invoice).exists()
        assert not PaymentInvoiceLineItem.objects.filter(invoice=other_invoice).exists()
