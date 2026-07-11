import datetime
from decimal import Decimal

import pytest

from commcare_connect.opportunity.management.commands.report_invoice_drift import (
    service_delivery_invoices,
)
from commcare_connect.opportunity.models import InvoiceStatus
from commcare_connect.opportunity.tests.factories import (
    CompletedWorkFactory,
    OpportunityAccessFactory,
    OpportunityFactory,
    PaymentInvoiceFactory,
    PaymentUnitFactory,
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
