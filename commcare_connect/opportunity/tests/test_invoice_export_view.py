import re
from datetime import date
from unittest import mock

import pytest
from django.urls import reverse

from commcare_connect.opportunity.forms import InvoiceExportForm
from commcare_connect.opportunity.models import InvoiceStatus
from commcare_connect.opportunity.tasks import generate_invoice_pdf_zip_export, generate_invoice_summary_export
from commcare_connect.opportunity.tests.factories import OpportunityFactory, PaymentInvoiceFactory
from commcare_connect.program.tests.factories import ProgramFactory
from config.celery_app import app as celery_app

JUNE = date(2026, 6, 1)


@pytest.fixture
def opportunity(organization):
    return OpportunityFactory(program=ProgramFactory(organization=organization), organization=organization)


def june_invoice(opportunity, **kwargs):
    kwargs.setdefault("service_delivery", True)
    kwargs.setdefault("start_date", JUNE)
    kwargs.setdefault("end_date", date(2026, 6, 30))
    kwargs.setdefault("date", date(2026, 7, 1))
    return PaymentInvoiceFactory(opportunity=opportunity, **kwargs)


@pytest.mark.django_db
class TestInvoiceExportForm:
    def _form(self, opportunity, **data):
        data.setdefault("export_type", InvoiceExportForm.PDF_ZIP)
        form = InvoiceExportForm(data=data, opportunity=opportunity)
        assert form.is_valid(), form.errors
        return form

    def test_selection_wins_over_the_month(self, opportunity):
        chosen = june_invoice(opportunity, invoice_number="A")
        june_invoice(opportunity, invoice_number="B")
        form = self._form(opportunity, invoice_ids=[chosen.pk], month="2026-06")
        assert list(form.get_invoices()) == [chosen]

    def test_month_scope_excludes_other_months_and_dead_statuses(self, opportunity):
        wanted = june_invoice(opportunity, invoice_number="A")
        june_invoice(opportunity, invoice_number="B", status=InvoiceStatus.CANCELLED_BY_NM)
        june_invoice(opportunity, invoice_number="C", start_date=date(2026, 7, 1), end_date=date(2026, 7, 31))
        form = self._form(opportunity, month="2026-06")
        assert list(form.get_invoices()) == [wanted]

    def test_all_months_scope(self, opportunity):
        june = june_invoice(opportunity, invoice_number="A")
        july = june_invoice(opportunity, invoice_number="C", start_date=date(2026, 7, 1), end_date=date(2026, 7, 31))
        form = self._form(opportunity, month="all")
        assert set(form.get_invoices()) == {june, july}

    def test_another_opportunitys_invoice_is_rejected(self, opportunity):
        other = PaymentInvoiceFactory()
        form = InvoiceExportForm(
            data={"export_type": InvoiceExportForm.PDF_ZIP, "invoice_ids": [other.pk]}, opportunity=opportunity
        )
        assert not form.is_valid()
        assert "invoice_ids" in form.errors

    def test_unknown_export_type_is_rejected(self, opportunity):
        form = InvoiceExportForm(data={"export_type": "exe"}, opportunity=opportunity)
        assert not form.is_valid()


@pytest.mark.django_db
class TestExportInvoicesView:
    def _url(self, opportunity, org_slug=None):
        return reverse(
            "opportunity:export_invoices",
            args=(org_slug or opportunity.organization.slug, opportunity.opportunity_id),
        )

    def _post(self, client, user, opportunity, **data):
        client.force_login(user)
        return client.post(self._url(opportunity), data)

    @pytest.mark.parametrize(
        "export_type, task",
        [
            (InvoiceExportForm.PDF_ZIP, generate_invoice_pdf_zip_export),
            (InvoiceExportForm.CSV_SUMMARY, generate_invoice_summary_export),
        ],
    )
    def test_dispatches_the_matching_task(self, client, opportunity, org_user_member, export_type, task):
        invoice = june_invoice(opportunity)
        with mock.patch.object(task, "delay") as delay:
            delay.return_value.id = "task-1"
            response = self._post(client, org_user_member, opportunity, export_type=export_type, month="2026-06")
        delay.assert_called_once_with(opportunity.pk, [invoice.pk])
        assert response.status_code == 302
        assert "export_task_id=task-1" in response.url
        assert "month=2026-06" in response.url

    def test_default_page_load_exports_only_the_month_on_screen(self, client, opportunity, org_user_member):
        """The list defaults to the newest month, so the export started from it must match."""
        june_invoice(opportunity, invoice_number="MAY", start_date=date(2026, 5, 1), end_date=date(2026, 5, 31))
        july = june_invoice(
            opportunity,
            invoice_number="JULY",
            start_date=date(2026, 7, 1),
            end_date=date(2026, 7, 31),
            date=date(2026, 8, 1),
        )
        client.force_login(org_user_member)
        listing = client.get(
            reverse("opportunity:invoice_list", args=(opportunity.organization.slug, opportunity.opportunity_id))
        )
        posted_month = re.search(r'name="month" value="([^"]*)"', listing.content.decode()).group(1)

        with mock.patch.object(generate_invoice_pdf_zip_export, "delay") as delay:
            self._post(client, org_user_member, opportunity, export_type=InvoiceExportForm.PDF_ZIP, month=posted_month)

        delay.assert_called_once_with(opportunity.pk, [july.pk])

    def test_selected_invoices_are_passed_through(self, client, opportunity, org_user_member):
        chosen = june_invoice(opportunity, invoice_number="A")
        june_invoice(opportunity, invoice_number="B")
        with mock.patch.object(generate_invoice_pdf_zip_export, "delay") as delay:
            self._post(
                client,
                org_user_member,
                opportunity,
                export_type=InvoiceExportForm.PDF_ZIP,
                invoice_ids=[chosen.pk],
            )
        delay.assert_called_once_with(opportunity.pk, [chosen.pk])

    def test_program_manager_can_export(
        self, client, managed_opportunity, program_manager_org, program_manager_org_user_admin
    ):
        """A program manager reaches the opportunity through their own org slug."""
        invoice = june_invoice(managed_opportunity)
        with mock.patch.object(generate_invoice_pdf_zip_export, "delay") as delay:
            delay.return_value.id = "task-1"
            client.force_login(program_manager_org_user_admin)
            response = client.post(
                self._url(managed_opportunity, org_slug=program_manager_org.slug),
                {"export_type": InvoiceExportForm.PDF_ZIP, "month": "2026-06"},
            )
        delay.assert_called_once_with(managed_opportunity.pk, [invoice.pk])
        assert response.status_code == 302

    def test_nothing_to_export_reports_an_error(self, client, opportunity, org_user_member):
        with mock.patch.object(generate_invoice_pdf_zip_export, "delay") as delay:
            response = self._post(
                client, org_user_member, opportunity, export_type=InvoiceExportForm.PDF_ZIP, month="2026-06"
            )
        delay.assert_not_called()
        assert response.status_code == 302
        assert "export_task_id" not in response.url

    def test_get_is_not_allowed(self, client, opportunity, org_user_member):
        client.force_login(org_user_member)
        assert client.get(self._url(opportunity)).status_code == 405


def _drop_cached_backend():
    """Celery caches the instantiated backend, so a conf change alone would not switch it."""
    try:
        del celery_app._local.backend
    except AttributeError:
        pass


@pytest.fixture
def eager_celery():
    """Run tasks in-process and keep their results where a poll by task id can find them."""
    previous_conf = (celery_app.conf.task_always_eager, celery_app.conf.result_backend)
    previous_store = generate_invoice_summary_export.store_eager_result
    celery_app.conf.task_always_eager = True
    celery_app.conf.result_backend = "cache+memory://"
    generate_invoice_summary_export.store_eager_result = True
    _drop_cached_backend()
    yield
    celery_app.conf.task_always_eager, celery_app.conf.result_backend = previous_conf
    generate_invoice_summary_export.store_eager_result = previous_store
    _drop_cached_backend()


@pytest.mark.django_db
def test_export_status_reports_the_finished_task(client, opportunity, org_user_member, eager_celery):
    """The toast polls by task id, so a finished export has to report complete and offer a file."""
    june_invoice(opportunity)
    client.force_login(org_user_member)
    org_slug = opportunity.organization.slug

    started = client.post(
        reverse("opportunity:export_invoices", args=(org_slug, opportunity.opportunity_id)),
        {"export_type": InvoiceExportForm.CSV_SUMMARY, "month": "2026-06"},
    )
    task_id = started.url.split("export_task_id=")[1].split("&")[0]

    status = client.get(reverse("opportunity:export_status", args=(org_slug, task_id)))

    body = status.content.decode()
    assert "Download Export" in body
    assert reverse("opportunity:download_export", args=(org_slug, task_id)) in body


@pytest.mark.django_db
def test_selection_bar_bindings_are_invoked_as_functions(client, opportunity, org_user_member):
    """The selection bar's helpers must be called, not read.

    They live on an object the template spreads into `x-data`, and spreading evaluates getters
    immediately against an object that has no selection yet. That throws, and Alpine then fails to
    start the component, so the bar never appears at all.
    """
    june_invoice(opportunity)
    client.force_login(org_user_member)

    body = client.get(
        reverse("opportunity:invoice_list", args=(opportunity.organization.slug, opportunity.opportunity_id))
    ).content.decode()

    assert "window.selectableTable" in body, "the shared select-all mixin is not on the page"
    for helper in ("selectedTotalUsd", "selectedUnpricedCount", "selectableRowCount"):
        assert f"{helper}()" in body
        assert f'"{helper}"' not in body
