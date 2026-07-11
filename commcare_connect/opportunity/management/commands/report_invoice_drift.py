import datetime

from commcare_connect.opportunity.models import InvoiceStatus, PaymentInvoice

EXCLUDED_STATUSES = [InvoiceStatus.CANCELLED_BY_NM, InvoiceStatus.REJECTED_BY_PM]

# Automated service-delivery invoicing only went live on 2026-01-01. Invoices generated before then were
# not produced by that pipeline, so the pre-agreement reconstruction does not apply — exclude them.
AUTOMATED_INVOICE_START_DATE = datetime.date(2026, 1, 1)


def service_delivery_invoices(opportunity_id=None, program_id=None, exclude_test=True):
    qs = (
        PaymentInvoice.objects.filter(
            service_delivery=True,
            end_date__isnull=False,
            date__gte=AUTOMATED_INVOICE_START_DATE,
        )
        .exclude(status__in=EXCLUDED_STATUSES)
        .filter(completedwork__isnull=False)
        .select_related("opportunity__organization")
        .distinct()
    )
    if exclude_test:
        qs = qs.filter(opportunity__is_test=False)
    if opportunity_id:
        qs = qs.filter(opportunity_id=opportunity_id)
    if program_id:
        qs = qs.filter(opportunity__program_id=program_id)
    return qs.order_by("opportunity_id", "id")
