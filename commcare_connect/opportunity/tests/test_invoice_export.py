import io
import zipfile
from datetime import date
from decimal import Decimal
from unittest import mock

import pytest

from commcare_connect.opportunity.models import InvoiceStatus
from commcare_connect.opportunity.tests.factories import (
    ExchangeRateFactory,
    PaymentFactory,
    PaymentInvoiceFactory,
)
from commcare_connect.opportunity.utils.invoice_export import (
    EXPORT_EXCLUDED_STATUSES,
    build_invoice_pdf_zip,
    build_invoice_summary_dataset,
    get_exportable_invoices,
    render_invoice_pdf,
)
from commcare_connect.utils.celery import export_content_type


@pytest.mark.django_db
class TestGetExportableInvoices:
    @pytest.mark.parametrize("status", [s for s in InvoiceStatus if s not in EXPORT_EXCLUDED_STATUSES])
    def test_includes_live_statuses(self, opportunity, status):
        invoice = PaymentInvoiceFactory(opportunity=opportunity, status=status)
        assert list(get_exportable_invoices(opportunity)) == [invoice]

    @pytest.mark.parametrize("status", EXPORT_EXCLUDED_STATUSES)
    def test_excludes_dead_statuses(self, opportunity, status):
        PaymentInvoiceFactory(opportunity=opportunity, status=status)
        assert not get_exportable_invoices(opportunity).exists()

    def test_scopes_to_month_and_opportunity(self, opportunity):
        in_month = PaymentInvoiceFactory(
            opportunity=opportunity, service_delivery=True, start_date=date(2026, 6, 1), end_date=date(2026, 6, 30)
        )
        PaymentInvoiceFactory(
            opportunity=opportunity, service_delivery=True, start_date=date(2026, 7, 1), end_date=date(2026, 7, 31)
        )
        PaymentInvoiceFactory(service_delivery=True, start_date=date(2026, 6, 1), end_date=date(2026, 6, 30))
        assert list(get_exportable_invoices(opportunity, date(2026, 6, 1))) == [in_month]


@pytest.mark.django_db
class TestPdfExport:
    def test_render_invoice_pdf(self, opportunity):
        invoice = PaymentInvoiceFactory(opportunity=opportunity, service_delivery=False, description="Fuel")
        assert render_invoice_pdf(invoice).startswith(b"%PDF")

    def test_zip_has_one_pdf_per_invoice_named_by_number(self, opportunity):
        invoices = [
            PaymentInvoiceFactory(opportunity=opportunity, service_delivery=False, invoice_number=number)
            for number in ("A1", "B2")
        ]
        progress = mock.Mock()

        content = build_invoice_pdf_zip(invoices, on_progress=progress)

        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            assert archive.namelist() == ["invoice_A1.pdf", "invoice_B2.pdf"]
            assert all(archive.read(name).startswith(b"%PDF") for name in archive.namelist())
        assert progress.call_args_list == [mock.call(1, 2), mock.call(2, 2)]

    def test_empty_zip(self):
        with zipfile.ZipFile(io.BytesIO(build_invoice_pdf_zip([]))) as archive:
            assert archive.namelist() == []


@pytest.mark.django_db
class TestSummaryDataset:
    def test_rows(self, opportunity):
        rate = ExchangeRateFactory(currency_code=opportunity.currency_code, rate=Decimal("129.43"))
        paid = PaymentInvoiceFactory(
            opportunity=opportunity,
            service_delivery=True,
            invoice_number="INV-1",
            status=InvoiceStatus.PAID,
            start_date=date(2026, 6, 1),
            end_date=date(2026, 6, 30),
            date=date(2026, 7, 2),
            amount=Decimal("767676.00"),
            amount_usd=Decimal("5931.80"),
            exchange_rate=rate,
        )
        PaymentFactory(
            invoice=paid, opportunity_access=None, organization=opportunity.organization, amount=paid.amount
        )
        custom = PaymentInvoiceFactory(
            opportunity=opportunity,
            service_delivery=False,
            invoice_number="INV-2",
            date_of_expense=date(2026, 7, 5),
            date=date(2026, 7, 6),
            amount=Decimal("100.00"),
            amount_usd=None,
            exchange_rate=None,
        )

        dataset = build_invoice_summary_dataset(get_exportable_invoices(opportunity))

        assert dataset.headers[:3] == ["Invoice Number", "Invoice Type", "Status"]
        rows = {row[0]: dict(zip(dataset.headers, row)) for row in dataset}
        assert rows["INV-1"]["Invoice Type"] == "Service Delivery"
        assert rows["INV-1"]["Status"] == "Paid"
        assert rows["INV-1"]["Period Start"] == date(2026, 6, 1)
        assert rows["INV-1"]["Currency"] == opportunity.currency_code
        assert rows["INV-1"]["Amount"] == Decimal("767676.00")
        assert rows["INV-1"]["Amount (USD)"] == Decimal("5931.80")
        assert rows["INV-1"]["Exchange Rate"] == Decimal("129.43")
        assert rows["INV-1"]["Payment Date"] == paid.payment.date_paid.date()
        assert rows["INV-2"]["Invoice Type"] == "Custom"
        assert rows["INV-2"]["Date of Expense"] == custom.date_of_expense
        assert rows["INV-2"]["Exchange Rate"] is None
        assert rows["INV-2"]["Payment Date"] is None
        assert "INV-1,Service Delivery,Paid" in dataset.export("csv")


@pytest.mark.parametrize(
    "filename, expected",
    [
        ("x_invoice_summary.csv", "text/csv; charset=utf-8"),
        ("x_invoice_pdfs.zip", "application/zip"),
        ("x.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        ("x.unknownext", "application/octet-stream"),
    ],
)
def test_export_content_type(filename, expected):
    assert export_content_type(filename) == expected
