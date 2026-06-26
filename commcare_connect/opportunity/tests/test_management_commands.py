import datetime

import pytest
from django.core.management import call_command

from commcare_connect.opportunity.models import (
    CompletedWorkStatus,
    InvoiceStatus,
    VisitReviewStatus,
    VisitValidationStatus,
)
from commcare_connect.opportunity.tests.factories import (
    CompletedWorkFactory,
    DeliverUnitFactory,
    OpportunityAccessFactory,
    OpportunityFactory,
    PaymentInvoiceFactory,
    PaymentUnitFactory,
    UserVisitFactory,
)
from commcare_connect.program.tests.factories import ManagedOpportunityFactory


@pytest.mark.django_db
class TestFixManagedOppApprovedCounts:
    def _make_visit(self, opp_access, deliver_unit, completed_work, review_status):
        return UserVisitFactory(
            opportunity=opp_access.opportunity,
            user=opp_access.user,
            opportunity_access=opp_access,
            deliver_unit=deliver_unit,
            completed_work=completed_work,
            status=VisitValidationStatus.approved,
            review_status=review_status,
        )

    def _setup_managed_opp_with_stale_count(self, invoice=None):
        opp_access = OpportunityAccessFactory(
            opportunity=ManagedOpportunityFactory(auto_approve_payments=True),
        )
        payment_unit = PaymentUnitFactory(opportunity=opp_access.opportunity, amount=100)
        deliver_unit = DeliverUnitFactory(app=opp_access.opportunity.deliver_app, payment_unit=payment_unit)
        cw = CompletedWorkFactory(
            status=CompletedWorkStatus.approved,
            opportunity_access=opp_access,
            payment_unit=payment_unit,
            invoice=invoice,
        )
        self._make_visit(opp_access, deliver_unit, cw, VisitReviewStatus.agree)
        self._make_visit(opp_access, deliver_unit, cw, VisitReviewStatus.pending)
        cw.saved_approved_count = 2
        cw.saved_payment_accrued = 200
        cw.save()
        return opp_access, cw

    def test_unbilled_work_saved_approved_count_corrected(self):
        opp_access, cw = self._setup_managed_opp_with_stale_count()

        call_command("fix_managed_opp_approved_counts", "--no-dry-run")

        cw.refresh_from_db()
        assert cw.saved_approved_count == 1
        assert cw.saved_payment_accrued == 100
        # The access-level total (which feeds invoicing) must be recomputed too.
        opp_access.refresh_from_db()
        assert opp_access.payment_accrued == 100

    def test_rerun_is_idempotent(self):
        opp_access, cw = self._setup_managed_opp_with_stale_count()

        call_command("fix_managed_opp_approved_counts", "--no-dry-run")
        call_command("fix_managed_opp_approved_counts", "--no-dry-run")

        cw.refresh_from_db()
        assert cw.saved_approved_count == 1
        assert cw.saved_payment_accrued == 100

    def test_dry_run_does_not_modify_data(self):
        opp_access, cw = self._setup_managed_opp_with_stale_count()

        call_command("fix_managed_opp_approved_counts", "--dry-run")

        cw.refresh_from_db()
        assert cw.saved_approved_count == 2
        assert cw.saved_payment_accrued == 200

    def test_work_with_count_of_one_not_reprocessed(self):
        opp_access = OpportunityAccessFactory(
            opportunity=ManagedOpportunityFactory(auto_approve_payments=True),
        )
        payment_unit = PaymentUnitFactory(opportunity=opp_access.opportunity, amount=100)
        deliver_unit = DeliverUnitFactory(app=opp_access.opportunity.deliver_app, payment_unit=payment_unit)
        cw = CompletedWorkFactory(
            status=CompletedWorkStatus.approved,
            opportunity_access=opp_access,
            payment_unit=payment_unit,
        )
        self._make_visit(opp_access, deliver_unit, cw, VisitReviewStatus.agree)
        cw.saved_approved_count = 1
        cw.saved_payment_accrued = 100
        cw.save()

        call_command("fix_managed_opp_approved_counts", "--no-dry-run")

        cw.refresh_from_db()
        # Count of 1 cannot be inflated by the bug; command should skip it
        assert cw.saved_approved_count == 1

    def test_inactive_opp_skipped(self):
        opp_access, cw = self._setup_managed_opp_with_stale_count()
        opp_access.opportunity.active = False
        opp_access.opportunity.save()

        call_command("fix_managed_opp_approved_counts", "--no-dry-run")

        cw.refresh_from_db()
        assert cw.saved_approved_count == 2
        assert cw.saved_payment_accrued == 200

    def test_billed_work_not_modified(self):
        invoice = PaymentInvoiceFactory()
        _, cw = self._setup_managed_opp_with_stale_count(invoice=invoice)

        call_command("fix_managed_opp_approved_counts", "--no-dry-run")

        cw.refresh_from_db()
        assert cw.saved_approved_count == 2
        assert cw.saved_payment_accrued == 200

    def test_non_managed_opp_not_modified(self):
        opp_access = OpportunityAccessFactory(opportunity__auto_approve_payments=True)
        payment_unit = PaymentUnitFactory(opportunity=opp_access.opportunity, amount=100)
        deliver_unit = DeliverUnitFactory(app=opp_access.opportunity.deliver_app, payment_unit=payment_unit)

        cw = CompletedWorkFactory(
            status=CompletedWorkStatus.approved,
            opportunity_access=opp_access,
            payment_unit=payment_unit,
        )
        self._make_visit(opp_access, deliver_unit, cw, VisitReviewStatus.agree)

        cw.saved_approved_count = 5
        cw.saved_payment_accrued = 500
        cw.save()

        call_command("fix_managed_opp_approved_counts", "--no-dry-run")

        cw.refresh_from_db()
        assert cw.saved_approved_count == 5
        assert cw.saved_payment_accrued == 500


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
