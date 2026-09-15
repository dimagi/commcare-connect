import io
import zipfile
from collections.abc import Callable

import weasyprint
from django.core.exceptions import SuspiciousFileOperation
from django.template.loader import render_to_string
from django.utils.text import get_valid_filename
from django.utils.translation import gettext_lazy
from django_weasyprint.utils import DjangoURLFetcher
from tablib import Dataset

from commcare_connect.opportunity.models import InvoiceStatus, PaymentInvoice
from commcare_connect.opportunity.utils.invoice import filter_invoices_by_month
from commcare_connect.opportunity.utils.invoice_line_items import get_invoice_service_summary

DIMAGI_ADDRESS = gettext_lazy("Dimagi, Inc.\n245 Main Street, 2nd Floor\nCambridge, MA 02142, USA\n+1 617.649.2214")

# Invoices that will never be paid are left out of a whole-month export.
EXPORT_EXCLUDED_STATUSES = (
    InvoiceStatus.CANCELLED_BY_NM,
    InvoiceStatus.REJECTED_BY_PM,
    InvoiceStatus.ARCHIVED,
)

# The URL fetcher serves anything under STATIC_URL straight from disk, so the base URL only has to
# turn `/static/...` into a `file:` URL. Rendering never touches the network.
PDF_BASE_URL = "file://"


def get_exportable_invoices(opportunity, month_start=None):
    """Invoices Finance can be handed for an opportunity, optionally scoped to one month."""
    queryset = (
        PaymentInvoice.objects.filter(opportunity=opportunity)
        .exclude(status__in=EXPORT_EXCLUDED_STATUSES)
        .select_related("opportunity", "exchange_rate", "payment")
    )
    if month_start:
        queryset = filter_invoices_by_month(queryset, month_start)
    return queryset.order_by("date", "invoice_number")


def build_invoice_pdf_zip(invoices, on_progress: Callable[[int, int], None] | None = None) -> bytes:
    """A zip with one PDF per invoice, named by invoice number. `on_progress(done, total)` is called after each."""
    invoices = list(invoices)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for index, invoice in enumerate(invoices, start=1):
            archive.writestr(invoice_pdf_filename(invoice), render_invoice_pdf(invoice))
            if on_progress:
                on_progress(index, len(invoices))
    return buffer.getvalue()


def render_invoice_pdf(invoice) -> bytes:
    html = render_to_string("opportunity/invoice_download.html", get_invoice_pdf_context(invoice))
    return weasyprint.HTML(string=html, base_url=PDF_BASE_URL, url_fetcher=DjangoURLFetcher()).write_pdf()


def get_invoice_pdf_context(invoice):
    return {
        "invoice": invoice,
        "opportunity": invoice.opportunity,
        "service_summary_lines": get_invoice_service_summary(invoice),
        "dimagi_address": DIMAGI_ADDRESS,
    }


def invoice_pdf_filename(invoice):
    """A safe filename for one invoice's PDF.

    The invoice number is user-supplied and only checked for reuse, so it can carry path
    separators or quotes. Both uses of this name are places that would hurt: a zip entry, where
    `../` escapes the extraction directory, and a Content-Disposition header, which a quote or a
    newline would break.
    """
    try:
        safe_number = get_valid_filename(invoice.invoice_number)
    except SuspiciousFileOperation:
        # Nothing survived sanitising (an all-punctuation number like "###"). Django raises rather
        # than return an empty name, and one such invoice must not take down a whole-month export.
        safe_number = str(invoice.pk)
    return f"invoice_{safe_number}.pdf"


def build_invoice_summary_dataset(invoices) -> Dataset:
    """One row per invoice with the amounts Finance reconciles against.

    Callers pass invoices that already have `opportunity`, `exchange_rate` and `payment` selected.
    """
    dataset = Dataset(
        headers=[
            "Invoice Number",
            "Invoice Type",
            "Status",
            "Period Start",
            "Period End",
            "Date of Expense",
            "Currency",
            "Amount",
            "Amount (USD)",
            "Exchange Rate",
            "Generated",
            "Payment Date",
        ]
    )
    for invoice in invoices:
        dataset.append(_summary_row(invoice))
    return dataset


def _summary_row(invoice):
    payment = getattr(invoice, "payment", None)
    return [
        invoice.invoice_number,
        invoice.invoice_type.label,
        invoice.get_status_display(),
        invoice.start_date,
        invoice.end_date,
        invoice.date_of_expense,
        invoice.opportunity.currency_code,
        invoice.amount,
        invoice.amount_usd,
        invoice.exchange_rate.rate if invoice.exchange_rate else None,
        invoice.date,
        payment.date_paid.date() if payment else None,
    ]
