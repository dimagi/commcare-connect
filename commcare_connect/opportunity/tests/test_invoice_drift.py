import datetime
from decimal import Decimal

import pytest

from commcare_connect.opportunity.management.commands.report_invoice_drift import (
    reconstruct_billed_count,
    service_delivery_invoices,
)
from commcare_connect.opportunity.models import InvoiceStatus, UserVisit, VisitReviewStatus, VisitValidationStatus
from commcare_connect.opportunity.tests.factories import (
    CompletedWorkFactory,
    DeliverUnitFactory,
    OpportunityAccessFactory,
    OpportunityFactory,
    PaymentInvoiceFactory,
    PaymentUnitFactory,
    UserVisitFactory,
)

pytestmark = pytest.mark.django_db

END = datetime.date(2026, 1, 31)


def _invoice_with_work(opportunity, *, amount, status=InvoiceStatus.PAID, service_delivery=True):
    invoice = PaymentInvoiceFactory(
        opportunity=opportunity,
        amount=Decimal(amount),
        service_delivery=service_delivery,
        status=status,
        end_date=END,
        date=END,
    )
    access = OpportunityAccessFactory(opportunity=opportunity)
    pu = PaymentUnitFactory(opportunity=opportunity, amount=100, org_amount=0)
    cw = CompletedWorkFactory(opportunity_access=access, payment_unit=pu, saved_approved_count=1)
    cw.invoice = invoice
    cw.save()
    return invoice, cw, pu


def test_query_excludes_cancelled_rejected_and_non_service_delivery(opportunity):
    live, _, _ = _invoice_with_work(opportunity, amount=100, status=InvoiceStatus.PAID)
    _invoice_with_work(opportunity, amount=100, status=InvoiceStatus.CANCELLED_BY_NM)
    _invoice_with_work(opportunity, amount=100, status=InvoiceStatus.REJECTED_BY_PM)
    _invoice_with_work(opportunity, amount=100, service_delivery=False)

    result = list(service_delivery_invoices())
    assert [i.id for i in result] == [live.id]


def test_query_excludes_test_opportunities_by_default(opportunity):
    real = OpportunityFactory(is_test=False)
    test_opp = OpportunityFactory(is_test=True)
    real_invoice, _, _ = _invoice_with_work(real, amount=100, status=InvoiceStatus.PAID)
    test_invoice, _, _ = _invoice_with_work(test_opp, amount=100, status=InvoiceStatus.PAID)

    assert [i.id for i in service_delivery_invoices()] == [real_invoice.id]
    assert test_invoice.id in {i.id for i in service_delivery_invoices(exclude_test=False)}


def _approved_visit(cw, deliver_unit, *, on, agree=False):
    """Create an approved visit and force status/status_modified_date/review_status (bypasses __setattr__).

    agree=True → review_status=agree (counts under the current agreement-gated logic / approved_count);
    agree=False → review_status=pending (counts only under the pre-agreement reconstruction).
    """
    visit = UserVisitFactory(
        opportunity=cw.opportunity_access.opportunity,
        opportunity_access=cw.opportunity_access,
        completed_work=cw,
        deliver_unit=deliver_unit,
    )
    UserVisit.objects.filter(pk=visit.pk).update(
        status=VisitValidationStatus.approved,
        status_modified_date=on,
        review_status=VisitReviewStatus.agree if agree else VisitReviewStatus.pending,
    )
    return visit


def test_reconstruct_counts_only_visits_approved_by_end_date(opportunity):
    access = OpportunityAccessFactory(opportunity=opportunity)
    pu = PaymentUnitFactory(opportunity=opportunity, amount=100, org_amount=0)
    du = DeliverUnitFactory(payment_unit=pu, optional=False)
    cw = CompletedWorkFactory(opportunity_access=access, payment_unit=pu)

    _approved_visit(cw, du, on=datetime.datetime(2026, 1, 15, tzinfo=datetime.UTC))
    _approved_visit(cw, du, on=datetime.datetime(2026, 2, 15, tzinfo=datetime.UTC))

    # Only the January visit is within the invoice window.
    assert reconstruct_billed_count(cw, END) == 1


def test_reconstruct_excludes_rejected_visits(opportunity):
    access = OpportunityAccessFactory(opportunity=opportunity)
    pu = PaymentUnitFactory(opportunity=opportunity, amount=100, org_amount=0)
    du = DeliverUnitFactory(payment_unit=pu, optional=False)
    cw = CompletedWorkFactory(opportunity_access=access, payment_unit=pu)

    visit = _approved_visit(cw, du, on=datetime.datetime(2026, 1, 15, tzinfo=datetime.UTC))
    # Post-invoice rejection overwrites status + status_modified_date.
    UserVisit.objects.filter(pk=visit.pk).update(
        status=VisitValidationStatus.rejected,
        status_modified_date=datetime.datetime(2026, 2, 15, tzinfo=datetime.UTC),
    )

    assert reconstruct_billed_count(cw, END) == 0


def test_reconstruct_child_constraint_uses_pre_agreement_basis(opportunity):
    """A child work approved-but-not-agreed still counts (legacy basis), unlike live approved_count."""
    access = OpportunityAccessFactory(opportunity=opportunity)
    parent_pu = PaymentUnitFactory(opportunity=opportunity, amount=100, org_amount=0)
    child_pu = PaymentUnitFactory(opportunity=opportunity, amount=0, org_amount=0)
    child_pu.parent_payment_unit = parent_pu
    child_pu.save()
    parent_du = DeliverUnitFactory(payment_unit=parent_pu, optional=False)
    child_du = DeliverUnitFactory(payment_unit=child_pu, optional=False)

    parent_cw = CompletedWorkFactory(opportunity_access=access, payment_unit=parent_pu, entity_id="e1")
    child_cw = CompletedWorkFactory(opportunity_access=access, payment_unit=child_pu, entity_id="e1")

    _approved_visit(parent_cw, parent_du, on=datetime.datetime(2026, 1, 15, tzinfo=datetime.UTC))
    child_visit = _approved_visit(child_cw, child_du, on=datetime.datetime(2026, 1, 15, tzinfo=datetime.UTC))
    # Child visit is approved but was never PM-agreed → excluded by live approved_count, kept by reconstruction.
    UserVisit.objects.filter(pk=child_visit.pk).update(review_status=VisitReviewStatus.pending)

    assert parent_cw.approved_count == 0  # live, agreement-gated logic drops it
    assert reconstruct_billed_count(parent_cw, END) == 1  # legacy basis keeps it, parent constrained by child
