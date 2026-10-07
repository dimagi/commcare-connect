import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from celery.exceptions import Retry
from django.urls import reverse

from commcare_connect.jira_service_desk import CustomerRequest, JiraServiceDeskNotConfigured
from commcare_connect.opportunity.forms import InvoiceFinanceRequestForm
from commcare_connect.opportunity.models import (
    Currency,
    InvoiceFinanceRequest,
    InvoiceFinanceRequestStatus,
    InvoiceStatus,
)
from commcare_connect.opportunity.tasks import FINANCE_REQUEST_MAX_RETRIES, submit_invoice_finance_request
from commcare_connect.opportunity.tests.factories import (
    InvoiceFinanceRequestFactory,
    OpportunityFactory,
    PaymentFactory,
    PaymentInvoiceFactory,
)
from commcare_connect.opportunity.utils.finance_request import (
    Question,
    build_form_answers,
    build_request_fields,
    can_submit_finance_request,
    get_additional_information_lines,
    get_previous_finance_request,
    suggest_new_vendor,
)
from commcare_connect.opportunity.views import get_finance_request_context
from commcare_connect.users.tests.factories import UserFactory


def _status_error(status_code, json=None):
    request = httpx.Request("POST", "https://jira.example/request")
    response = httpx.Response(status_code, json=json or {}, request=request)
    return httpx.HTTPStatusError("error", request=request, response=response)


@pytest.fixture
def invoice(opportunity):
    opportunity.organization.name = "Health Partners"
    opportunity.organization.save()
    opportunity.name = "Malaria Outreach"
    opportunity.currency = Currency.objects.get(code="KES")
    opportunity.save()
    return PaymentInvoiceFactory(
        opportunity=opportunity,
        invoice_number="INV-7",
        service_delivery=False,
        description="Community training",
        amount=Decimal("1500.00"),
        amount_usd=Decimal("11.60"),
        start_date=datetime.date(2026, 6, 1),
        end_date=datetime.date(2026, 6, 30),
        status=InvoiceStatus.READY_TO_PAY,
    )


@pytest.fixture
def finance_request(invoice):
    submitter = UserFactory(name="Pat Manager", email="pat@example.com")
    return InvoiceFinanceRequestFactory(invoice=invoice, submitted_by=submitter, gl_account="66")


def test_request_fields(finance_request):
    assert build_request_fields(finance_request) == {"summary": "Connect Payment request: Health Partners June 2026"}


def test_form_answers_for_direct_account(finance_request):
    answers = build_form_answers(finance_request, "temp-1")

    assert {question: answer for question, answer in answers.items() if question != "59"} == {
        Question.PRIORITY: {"choices": ["3"]},
        Question.APPROVED: {"choices": ["1"]},
        Question.APPROVER_NAME: {"text": "Pat Manager (pat@example.com)"},
        Question.CONTRACTED_ENTITY: {"choices": ["1"]},
        Question.STATEMENT_PERIOD: {"text": "June 2026"},
        Question.NEW_VENDOR: {"choices": ["2"]},
        Question.INVOICE_UPLOAD: {"files": ["temp-1"]},
        Question.GL_ACCOUNT: {"choices": ["66"]},
        Question.PROJECT_NAME: {"text": "Connect Delivery 2026"},
        Question.SUBDIVISION: {"choices": ["50"]},
        Question.MULTIPLE_ACCOUNTS: {"choices": ["2"]},
        Question.BRIEF_DESCRIPTION: {"text": "Connect payment to Health Partners for Malaria Outreach, invoice INV-7"},
    }
    paragraphs = answers[Question.ADDITIONAL_INFORMATION]["adf"]["content"]
    assert [p["content"][0]["text"] for p in paragraphs] == get_additional_information_lines(finance_request)


def test_form_answers_leave_out_project_name_for_overhead_account(finance_request):
    finance_request.gl_account = "50"

    assert Question.PROJECT_NAME not in build_form_answers(finance_request, "temp-1")


def test_form_answers_for_new_vendor(finance_request):
    finance_request.new_vendor = True

    assert build_form_answers(finance_request, "temp-1")[Question.NEW_VENDOR] == {"choices": ["1"]}


def test_additional_information_uses_pm_approval(finance_request):
    finance_request.invoice.__dict__["pm_certification"] = {
        "name": "Ada Approver (ada@example.com)",
        "certified_at": datetime.datetime(2026, 7, 2, 10, 0, tzinfo=datetime.UTC),
    }

    assert get_additional_information_lines(finance_request) == [
        "Vendor: Health Partners",
        "Connect Opportunity: Malaria Outreach",
        "Invoice: INV-7",
        "Description of services:",
        "- Community training: KES 1,500.00",
        "Total: KES 1,500.00 (USD 11.60)",
        "Approved by Ada Approver (ada@example.com) on July 2, 2026",
        "Submitted for CommCare Connect by Pat Manager (pat@example.com)",
    ]


def test_additional_information_without_approval_or_usd(finance_request):
    finance_request.invoice.amount_usd = None

    lines = get_additional_information_lines(finance_request)

    assert lines[-3:] == [
        "Total: KES 1,500.00",
        "Approved by Pat Manager (pat@example.com)",
        "Submitted for CommCare Connect by Pat Manager (pat@example.com)",
    ]


@pytest.mark.parametrize(
    "invoice_status, request_status, expected",
    [
        (InvoiceStatus.READY_TO_PAY, None, True),
        (InvoiceStatus.READY_TO_PAY, InvoiceFinanceRequestStatus.FAILED, True),
        (InvoiceStatus.READY_TO_PAY, InvoiceFinanceRequestStatus.PENDING, False),
        (InvoiceStatus.READY_TO_PAY, InvoiceFinanceRequestStatus.SUBMITTED, False),
        (InvoiceStatus.PENDING_PM_REVIEW, None, False),
        (InvoiceStatus.PAID, None, False),
    ],
)
def test_can_submit_finance_request(invoice, invoice_status, request_status, expected):
    invoice.status = invoice_status
    if request_status:
        InvoiceFinanceRequestFactory(invoice=invoice, status=request_status)

    assert can_submit_finance_request(invoice) is expected


class TestSuggestNewVendor:
    def test_first_payment(self, invoice):
        assert suggest_new_vendor(invoice.opportunity.organization) is True

    def test_paid_before_on_another_opportunity(self, invoice):
        other_opportunity = OpportunityFactory(organization=invoice.opportunity.organization)
        PaymentFactory(invoice=PaymentInvoiceFactory(opportunity=other_opportunity), opportunity_access=None)

        assert suggest_new_vendor(invoice.opportunity.organization) is False

    @pytest.mark.parametrize(
        "status, expected",
        [(InvoiceFinanceRequestStatus.SUBMITTED, False), (InvoiceFinanceRequestStatus.FAILED, True)],
    )
    def test_requested_before(self, invoice, status, expected):
        InvoiceFinanceRequestFactory(invoice=PaymentInvoiceFactory(opportunity=invoice.opportunity), status=status)

        assert suggest_new_vendor(invoice.opportunity.organization) is expected


def test_previous_finance_request_is_the_latest_on_the_opportunity(invoice):
    InvoiceFinanceRequestFactory(invoice=PaymentInvoiceFactory(opportunity=invoice.opportunity))
    latest = InvoiceFinanceRequestFactory(invoice=PaymentInvoiceFactory(opportunity=invoice.opportunity))
    InvoiceFinanceRequestFactory()  # another opportunity

    assert get_previous_finance_request(invoice.opportunity) == latest


class TestSubmitInvoiceFinanceRequest:
    @pytest.fixture(autouse=True)
    def jira(self):
        with (
            patch("commcare_connect.opportunity.tasks.render_invoice_pdf", return_value=b"%PDF") as render,
            patch("commcare_connect.opportunity.tasks.attach_temporary_file", return_value="temp-1") as attach,
            patch(
                "commcare_connect.opportunity.tasks.create_request",
                return_value=CustomerRequest(key="FIN-9", url="https://jira.example/FIN-9"),
            ) as create,
        ):
            yield SimpleNamespace(render=render, attach=attach, create=create)

    def test_submits_and_links_ticket(self, finance_request, jira):
        submit_invoice_finance_request(finance_request.id)

        finance_request.refresh_from_db()
        assert finance_request.status == InvoiceFinanceRequestStatus.SUBMITTED
        assert finance_request.issue_key == "FIN-9"
        finance_request.invoice.refresh_from_db()
        assert finance_request.invoice.invoice_ticket_link == "https://jira.example/FIN-9"
        jira.attach.assert_called_once_with("19", "invoice_INV-7.pdf", b"%PDF", "application/pdf")
        service_desk, request_type, fields, answers = jira.create.call_args.args
        assert (service_desk, request_type) == ("19", "186")
        assert fields == build_request_fields(finance_request)
        assert answers[Question.INVOICE_UPLOAD] == {"files": ["temp-1"]}

    @pytest.mark.parametrize("status", [InvoiceFinanceRequestStatus.SUBMITTED, InvoiceFinanceRequestStatus.FAILED])
    def test_only_pending_requests_are_sent(self, finance_request, jira, status):
        finance_request.status = status
        finance_request.save()

        submit_invoice_finance_request(finance_request.id)

        jira.attach.assert_not_called()
        jira.create.assert_not_called()

    def test_unconfigured(self, finance_request, jira):
        jira.attach.side_effect = JiraServiceDeskNotConfigured

        submit_invoice_finance_request(finance_request.id)

        finance_request.refresh_from_db()
        assert finance_request.status == InvoiceFinanceRequestStatus.FAILED
        assert finance_request.error == "Submitting to Finance is not configured."
        jira.create.assert_not_called()

    def test_rejected_request_shows_jira_message(self, finance_request, jira):
        jira.create.side_effect = _status_error(422, {"errorMessage": "Some fields have invalid entries."})

        submit_invoice_finance_request(finance_request.id)

        finance_request.refresh_from_db()
        assert finance_request.status == InvoiceFinanceRequestStatus.FAILED
        assert finance_request.error == "Jira rejected the request: Some fields have invalid entries."

    @pytest.mark.parametrize(
        "step, error",
        [
            ("attach", httpx.ReadTimeout("slow")),
            ("attach", _status_error(503)),
            ("create", httpx.ConnectError("down")),
            ("create", _status_error(429)),
        ],
    )
    def test_transient_errors_are_retried(self, finance_request, jira, step, error):
        getattr(jira, step).side_effect = error

        with patch.object(submit_invoice_finance_request, "retry", side_effect=Retry) as retry:
            with pytest.raises(Retry):
                submit_invoice_finance_request(finance_request.id)

        assert retry.call_args.kwargs == {"exc": error, "countdown": 60}
        finance_request.refresh_from_db()
        assert finance_request.status == InvoiceFinanceRequestStatus.PENDING

    @pytest.mark.parametrize("error", [httpx.ReadTimeout("slow"), _status_error(502)])
    def test_create_that_may_have_reached_jira_is_not_resent(self, finance_request, jira, error):
        jira.create.side_effect = error

        submit_invoice_finance_request(finance_request.id)

        finance_request.refresh_from_db()
        assert finance_request.status == InvoiceFinanceRequestStatus.FAILED
        assert "may have been created" in finance_request.error

    @pytest.mark.parametrize(
        "retries",
        [
            FINANCE_REQUEST_MAX_RETRIES,
            0,  # run inline, as in local development, where a retry can't wait
        ],
    )
    def test_fails_without_retrying_when_retries_are_exhausted_or_inline(self, finance_request, jira, retries):
        jira.attach.side_effect = httpx.ConnectError("down")

        submit_invoice_finance_request.apply(args=[finance_request.id], retries=retries)

        finance_request.refresh_from_db()
        assert finance_request.status == InvoiceFinanceRequestStatus.FAILED
        assert finance_request.error == "Jira could not be reached. Try again later."


class TestInvoiceFinanceRequestForm:
    def _form(self, **data):
        data = {"contracted_entity": "1", "gl_account": "66", "project_name": "", "new_vendor": "False", **data}
        return InvoiceFinanceRequestForm(data=data)

    def test_direct_account_requires_project_name(self):
        form = self._form(gl_account="66", project_name="  ")

        assert not form.is_valid()
        assert "project_name" in form.errors

    def test_overhead_account_drops_project_name(self):
        form = self._form(gl_account="50", project_name="Not needed")

        assert form.is_valid(), form.errors
        assert form.cleaned_data["project_name"] == ""

    @pytest.mark.parametrize("field, value", [("gl_account", "11"), ("contracted_entity", "9")])
    def test_rejects_choices_outside_the_offered_lists(self, field, value):
        assert field in self._form(**{field: value}).errors

    @pytest.mark.parametrize("value, expected", [("True", True), ("False", False)])
    def test_new_vendor(self, value, expected):
        form = self._form(new_vendor=value, project_name="Project")

        assert form.is_valid(), form.errors
        assert form.cleaned_data["new_vendor"] is expected


class TestFinanceRequestContext:
    @pytest.fixture(autouse=True)
    def configured(self, settings):
        settings.JIRA_SERVICE_DESK_CLIENT_ID = "client-id"
        settings.JIRA_SERVICE_DESK_CLIENT_SECRET = "client-secret"

    def test_prefills_from_the_opportunitys_last_request(self, invoice):
        InvoiceFinanceRequestFactory(
            invoice=PaymentInvoiceFactory(opportunity=invoice.opportunity),
            contracted_entity="2",
            gl_account="1",
            project_name="Malaria 2026",
            status=InvoiceFinanceRequestStatus.SUBMITTED,
        )

        form = get_finance_request_context(invoice)["finance_request_form"]

        assert form.initial == {
            "contracted_entity": "2",
            "gl_account": "1",
            "project_name": "Malaria 2026",
            "new_vendor": False,
        }

    def test_first_request_suggests_new_vendor(self, invoice):
        form = get_finance_request_context(invoice)["finance_request_form"]

        assert form.initial == {"new_vendor": True}

    def test_no_form_when_unconfigured(self, invoice, settings):
        settings.JIRA_SERVICE_DESK_CLIENT_ID = None

        context = get_finance_request_context(invoice)

        assert context["finance_request_form"] is None
        assert context["finance_request_configured"] is False


@pytest.mark.django_db
class TestInvoiceFinanceRequestView:
    DATA = {"contracted_entity": "1", "gl_account": "66", "project_name": "Malaria 2026", "new_vendor": "False"}

    @pytest.fixture(autouse=True)
    def configured(self, settings):
        settings.JIRA_SERVICE_DESK_CLIENT_ID = "client-id"
        settings.JIRA_SERVICE_DESK_CLIENT_SECRET = "client-secret"

    @pytest.fixture
    def pm_invoice(self, managed_opportunity):
        return PaymentInvoiceFactory(opportunity=managed_opportunity, status=InvoiceStatus.READY_TO_PAY)

    def _post(self, client, user, invoice, org_slug, data=None):
        client.force_login(user)
        url = reverse(
            "opportunity:invoice_finance_request",
            args=(org_slug, invoice.opportunity.opportunity_id, invoice.payment_invoice_id),
        )
        return client.post(url, data or self.DATA)

    def test_pm_sends_invoice(
        self,
        client,
        pm_invoice,
        program_manager_org,
        program_manager_org_user_admin,
        django_capture_on_commit_callbacks,
    ):
        with patch.object(submit_invoice_finance_request, "delay") as delay:
            with django_capture_on_commit_callbacks(execute=True):
                response = self._post(client, program_manager_org_user_admin, pm_invoice, program_manager_org.slug)

        assert response.status_code == 200
        finance_request = InvoiceFinanceRequest.objects.get(invoice=pm_invoice)
        assert finance_request.status == InvoiceFinanceRequestStatus.PENDING
        assert finance_request.submitted_by == program_manager_org_user_admin
        assert (finance_request.contracted_entity, finance_request.gl_account) == ("1", "66")
        delay.assert_called_once_with(finance_request.id)

    def test_retry_reuses_failed_request(
        self,
        client,
        pm_invoice,
        program_manager_org,
        program_manager_org_user_admin,
        django_capture_on_commit_callbacks,
    ):
        failed = InvoiceFinanceRequestFactory(
            invoice=pm_invoice, status=InvoiceFinanceRequestStatus.FAILED, error="Jira could not be reached."
        )
        with patch.object(submit_invoice_finance_request, "delay") as delay:
            with django_capture_on_commit_callbacks(execute=True):
                self._post(client, program_manager_org_user_admin, pm_invoice, program_manager_org.slug)

        failed.refresh_from_db()
        assert (failed.status, failed.error) == (InvoiceFinanceRequestStatus.PENDING, "")
        delay.assert_called_once_with(failed.id)

    def test_invalid_answers_create_nothing(
        self, client, pm_invoice, program_manager_org, program_manager_org_user_admin
    ):
        response = self._post(
            client,
            program_manager_org_user_admin,
            pm_invoice,
            program_manager_org.slug,
            data={**self.DATA, "project_name": ""},
        )

        assert response.status_code == 200
        assert "A project name is required" in response.content.decode()
        assert not InvoiceFinanceRequest.objects.filter(invoice=pm_invoice).exists()

    @pytest.mark.parametrize(
        "invoice_status, existing_status",
        [
            (InvoiceStatus.PENDING_PM_REVIEW, None),
            (InvoiceStatus.READY_TO_PAY, InvoiceFinanceRequestStatus.SUBMITTED),
        ],
    )
    def test_ineligible_invoice_is_not_sent(
        self,
        client,
        pm_invoice,
        program_manager_org,
        program_manager_org_user_admin,
        invoice_status,
        existing_status,
    ):
        pm_invoice.status = invoice_status
        pm_invoice.save()
        if existing_status:
            InvoiceFinanceRequestFactory(invoice=pm_invoice, status=existing_status)

        with patch.object(submit_invoice_finance_request, "delay") as delay:
            self._post(client, program_manager_org_user_admin, pm_invoice, program_manager_org.slug)

        delay.assert_not_called()
        assert (
            InvoiceFinanceRequest.objects.filter(
                invoice=pm_invoice, status=InvoiceFinanceRequestStatus.PENDING
            ).count()
            == 0
        )

    def test_network_manager_cannot_send(self, client, pm_invoice, org_user_admin):
        response = self._post(client, org_user_admin, pm_invoice, pm_invoice.opportunity.organization.slug)

        assert response.status_code == 404
        assert not InvoiceFinanceRequest.objects.filter(invoice=pm_invoice).exists()

    @pytest.mark.parametrize("jira_configured, expected", [(True, "Send to Finance"), (False, "not configured")])
    def test_invoice_page_shows_finance_card(
        self,
        client,
        settings,
        pm_invoice,
        program_manager_org,
        program_manager_org_user_admin,
        jira_configured,
        expected,
    ):
        if not jira_configured:
            settings.JIRA_SERVICE_DESK_CLIENT_ID = None
        client.force_login(program_manager_org_user_admin)

        response = client.get(
            reverse(
                "opportunity:invoice_review",
                args=(program_manager_org.slug, pm_invoice.opportunity.opportunity_id, pm_invoice.payment_invoice_id),
            )
        )

        assert response.status_code == 200
        assert expected in response.content.decode()
