import datetime

import pytest
from django.core.management import CommandError, call_command

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


@pytest.fixture
def approved_visit_with_completed_work(db):
    """Stuck managed-opportunity visit: status=approved, review_status=agree,
    review_created_on=null. The parent CompletedWork is approved with cached
    payment, and not linked to any invoice.
    """
    opp_access = OpportunityAccessFactory(
        opportunity=ManagedOpportunityFactory(auto_approve_payments=True),
    )
    payment_unit = PaymentUnitFactory(opportunity=opp_access.opportunity, amount=100)
    deliver_unit = DeliverUnitFactory(
        app=opp_access.opportunity.deliver_app,
        payment_unit=payment_unit,
    )
    completed_work = CompletedWorkFactory(
        status=CompletedWorkStatus.approved,
        opportunity_access=opp_access,
        payment_unit=payment_unit,
        saved_approved_count=1,
        saved_completed_count=1,
        saved_payment_accrued=100,
    )
    visit = UserVisitFactory(
        opportunity=opp_access.opportunity,
        user=opp_access.user,
        opportunity_access=opp_access,
        deliver_unit=deliver_unit,
        completed_work=completed_work,
        status=VisitValidationStatus.approved,
        review_status=VisitReviewStatus.agree,
        review_created_on=None,
    )
    return visit


@pytest.mark.django_db
class TestRejectApprovedVisit:
    def _call(self, opp, visit, **overrides):
        args = [
            "reject_approved_visit",
            "--opportunity-id",
            str(opp.opportunity_id),
            "--visit-id",
            str(visit.user_visit_id),
            "--reason",
            "Manual ops fix per CCCT-XXXX",
            "--actor-email",
            "ops@dimagi.com",
        ]
        for k, v in overrides.items():
            args.extend([f"--{k.replace('_', '-')}", v])
        call_command(*args)

    def test_rejects_approved_visit_and_zeros_completed_work_payment(self, approved_visit_with_completed_work):
        visit = approved_visit_with_completed_work
        completed_work = visit.completed_work
        opp = visit.opportunity

        self._call(opp, visit)

        visit.refresh_from_db()
        completed_work.refresh_from_db()
        assert visit.status == VisitValidationStatus.rejected
        assert visit.reason == "Manual ops fix per CCCT-XXXX"
        assert completed_work.status == CompletedWorkStatus.rejected
        assert completed_work.saved_payment_accrued == 0

    def test_refuses_when_opportunity_not_found(self, approved_visit_with_completed_work):
        visit = approved_visit_with_completed_work
        bogus_opp = type("X", (), {"opportunity_id": "00000000-0000-0000-0000-000000000000"})
        with pytest.raises(CommandError, match="Opportunity .* not found"):
            self._call(bogus_opp, visit)

    def test_refuses_when_visit_not_in_opportunity(self, approved_visit_with_completed_work):
        visit = approved_visit_with_completed_work
        other_opp = OpportunityFactory()
        with pytest.raises(CommandError, match="Visit .* not found"):
            self._call(other_opp, visit)

    def test_refuses_when_opportunity_not_managed(self, db):
        opp_access = OpportunityAccessFactory()  # plain (non-managed) opportunity
        assert not opp_access.opportunity.managed
        payment_unit = PaymentUnitFactory(opportunity=opp_access.opportunity, amount=100)
        deliver_unit = DeliverUnitFactory(app=opp_access.opportunity.deliver_app, payment_unit=payment_unit)
        completed_work = CompletedWorkFactory(
            status=CompletedWorkStatus.approved, opportunity_access=opp_access, payment_unit=payment_unit
        )
        visit = UserVisitFactory(
            opportunity=opp_access.opportunity,
            user=opp_access.user,
            opportunity_access=opp_access,
            deliver_unit=deliver_unit,
            completed_work=completed_work,
            status=VisitValidationStatus.approved,
        )
        with pytest.raises(CommandError, match="not a managed opportunity"):
            self._call(opp_access.opportunity, visit)

    def test_refuses_when_visit_already_rejected(self, approved_visit_with_completed_work):
        visit = approved_visit_with_completed_work
        visit.status = VisitValidationStatus.rejected
        visit.save(update_fields=["status"])
        with pytest.raises(CommandError, match="not in 'approved' status"):
            self._call(visit.opportunity, visit)

    def test_refuses_when_visit_status_pending(self, approved_visit_with_completed_work):
        visit = approved_visit_with_completed_work
        visit.status = VisitValidationStatus.pending
        visit.save(update_fields=["status"])
        with pytest.raises(CommandError, match="not in 'approved' status"):
            self._call(visit.opportunity, visit)

    def test_refuses_when_visit_has_no_completed_work(self, approved_visit_with_completed_work):
        visit = approved_visit_with_completed_work
        visit.completed_work = None
        visit.save(update_fields=["completed_work"])
        with pytest.raises(CommandError, match="no linked CompletedWork"):
            self._call(visit.opportunity, visit)

    def test_refuses_when_completed_work_linked_to_invoice(self, approved_visit_with_completed_work):
        visit = approved_visit_with_completed_work
        completed_work = visit.completed_work
        invoice = PaymentInvoiceFactory(opportunity=visit.opportunity)
        completed_work.invoice = invoice
        completed_work.save(update_fields=["invoice"])
        with pytest.raises(CommandError, match="already linked to invoice"):
            self._call(visit.opportunity, visit)

    def test_works_on_suspended_access(self, approved_visit_with_completed_work):
        """Suspending a worker is a common precursor to wanting to withhold
        their pending payments, so the command must still cascade the
        CompletedWork recompute even when the access is suspended.
        """
        visit = approved_visit_with_completed_work
        completed_work = visit.completed_work
        access = visit.opportunity_access
        access.suspended = True
        access.save(update_fields=["suspended"])

        self._call(visit.opportunity, visit)

        visit.refresh_from_db()
        completed_work.refresh_from_db()
        assert visit.status == VisitValidationStatus.rejected
        assert completed_work.status == CompletedWorkStatus.rejected
        assert completed_work.saved_payment_accrued == 0

    def test_dry_run_does_not_mutate(self, approved_visit_with_completed_work):
        visit = approved_visit_with_completed_work
        completed_work = visit.completed_work

        call_command(
            "reject_approved_visit",
            "--opportunity-id",
            str(visit.opportunity.opportunity_id),
            "--visit-id",
            str(visit.user_visit_id),
            "--reason",
            "dry-run preview",
            "--actor-email",
            "ops@dimagi.com",
            "--dry-run",
        )

        visit.refresh_from_db()
        completed_work.refresh_from_db()
        assert visit.status == VisitValidationStatus.approved
        assert completed_work.status == CompletedWorkStatus.approved
        assert completed_work.saved_payment_accrued == 100

    def test_no_mutation_when_check_fails(self, approved_visit_with_completed_work):
        visit = approved_visit_with_completed_work
        completed_work = visit.completed_work
        invoice = PaymentInvoiceFactory(opportunity=visit.opportunity)
        completed_work.invoice = invoice
        completed_work.save(update_fields=["invoice"])

        with pytest.raises(CommandError):
            self._call(visit.opportunity, visit)

        visit.refresh_from_db()
        completed_work.refresh_from_db()
        assert visit.status == VisitValidationStatus.approved
        assert completed_work.status == CompletedWorkStatus.approved
        assert completed_work.saved_payment_accrued == 100
