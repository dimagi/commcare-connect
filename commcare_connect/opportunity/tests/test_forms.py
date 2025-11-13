import datetime
from unittest.mock import patch

import pytest
from waffle.testutils import override_switch

from commcare_connect.flags.switch_names import OPPORTUNITY_CREDENTIALS
from commcare_connect.opportunity.forms import AddBudgetNewUsersForm, OpportunityChangeForm, PaymentInvoiceForm
from commcare_connect.opportunity.models import (
    CompletedWork,
    CompletedWorkStatus,
    CredentialConfiguration,
    PaymentUnit,
)
from commcare_connect.opportunity.tests.factories import (
    CommCareAppFactory,
    CompletedWorkFactory,
    ExchangeRateFactory,
    OpportunityFactory,
    PaymentInvoiceFactory,
    PaymentUnitFactory,
)
from commcare_connect.program.tests.factories import ManagedOpportunityFactory, ProgramFactory


@pytest.fixture
def valid_opportunity(organization):
    opp = OpportunityFactory(
        organization=organization,
        active=True,
        learn_app=CommCareAppFactory(cc_app_id="test_learn_app"),
        deliver_app=CommCareAppFactory(cc_app_id="test_deliver_app"),
        name="Test Opportunity",
        description="Test Description",
        short_description="Short Description",
        currency="USD",
        is_test=False,
        end_date=datetime.date.today() + datetime.timedelta(days=30),
    )
    PaymentUnitFactory(opportunity=opp)
    return opp


@pytest.mark.django_db
class TestOpportunityChangeForm:
    @pytest.fixture
    def base_form_data(self, valid_opportunity):
        return {
            "name": "Updated Opportunity",
            "description": "Updated Description",
            "short_description": "Updated Short Description",
            "active": True,
            "currency": "EUR",
            "is_test": False,
            "delivery_type": valid_opportunity.delivery_type.id,
            "end_date": (datetime.date.today() + datetime.timedelta(days=60)).isoformat(),
            "users": "+1234567890\n+9876543210",
            "learn_level": None,
            "deliver_level": None,
        }

    def test_form_initialization(self, valid_opportunity):
        form = OpportunityChangeForm(instance=valid_opportunity)
        expected_fields = {
            "name",
            "description",
            "short_description",
            "active",
            "currency",
            "is_test",
            "delivery_type",
            "end_date",
            "users",
        }
        assert all(field in form.fields for field in expected_fields)

        expected_initial = {
            "name": valid_opportunity.name,
            "description": valid_opportunity.description,
            "short_description": valid_opportunity.short_description,
            "active": valid_opportunity.active,
            "currency": valid_opportunity.currency,
            "is_test": valid_opportunity.is_test,
            "delivery_type": valid_opportunity.delivery_type.id,
            "end_date": valid_opportunity.end_date.isoformat(),
        }
        assert all(form.initial.get(key) == value for key, value in expected_initial.items())

    @pytest.mark.parametrize(
        "field",
        [
            "name",
            "description",
            "short_description",
            "currency",
        ],
    )
    def test_required_fields(self, valid_opportunity, field, base_form_data):
        data = base_form_data.copy()
        data[field] = ""
        form = OpportunityChangeForm(data=data, instance=valid_opportunity)
        assert not form.is_valid()
        assert field in form.errors

    @pytest.mark.parametrize(
        "test_data",
        [
            pytest.param(
                {
                    "field": "end_date",
                    "value": "invalid-date",
                    "error_expected": True,
                    "error_message": "Enter a valid date.",
                },
                id="invalid_end_date",
            ),
            pytest.param(
                {
                    "field": "users",
                    "value": "  +1234567890  \n  +9876543210  ",
                    "error_expected": False,
                    "expected_clean": ["+1234567890", "+9876543210"],
                },
                id="valid_users_with_whitespace",
            ),
        ],
    )
    def test_field_validation(self, valid_opportunity, base_form_data, test_data):
        data = base_form_data.copy()
        data[test_data["field"]] = test_data["value"]
        form = OpportunityChangeForm(data=data, instance=valid_opportunity)
        if test_data["error_expected"]:
            assert not form.is_valid()
            assert test_data["error_message"] in str(form.errors[test_data["field"]])
        else:
            assert form.is_valid()
            if "expected_clean" in test_data:
                assert form.cleaned_data[test_data["field"]] == test_data["expected_clean"]

    @pytest.mark.parametrize(
        "app_scenario",
        [
            pytest.param(
                {
                    "active_app_ids": ("unique_app1", "unique_app2"),
                    "new_app_ids": ("different_app1", "different_app2"),
                    "expected_valid": True,
                },
                id="unique_apps",
            ),
            pytest.param(
                {
                    "active_app_ids": ("shared_app1", "shared_app2"),
                    "new_app_ids": ("shared_app1", "shared_app2"),
                    "expected_valid": False,
                },
                id="reused_apps",
            ),
        ],
    )
    def test_app_reuse_validation(self, organization, base_form_data, app_scenario):
        opp1 = OpportunityFactory(
            organization=organization,
            active=True,
            learn_app=CommCareAppFactory(cc_app_id=app_scenario["active_app_ids"][0]),
            deliver_app=CommCareAppFactory(cc_app_id=app_scenario["active_app_ids"][1]),
        )
        PaymentUnitFactory(opportunity=opp1)

        inactive_opp = OpportunityFactory(
            organization=organization,
            active=False,
            learn_app=CommCareAppFactory(cc_app_id=app_scenario["new_app_ids"][0]),
            deliver_app=CommCareAppFactory(cc_app_id=app_scenario["new_app_ids"][1]),
        )

        PaymentUnitFactory(opportunity=inactive_opp)

        form = OpportunityChangeForm(data=base_form_data, instance=inactive_opp)

        assert form.is_valid() == app_scenario["expected_valid"]
        if not app_scenario["expected_valid"]:
            assert "Cannot reactivate opportunity with reused applications" in str(form.errors["active"])

    @pytest.mark.parametrize(
        "data_updates,expected_valid",
        [
            ({"currency": "USD", "additional_users": 5}, True),
            ({"currency": "EUR", "additional_users": 10}, True),
            ({"currency": "INVALID", "additional_users": 5}, False),
            ({"currency": "USD", "additional_users": -5}, True),
        ],
    )
    def test_valid_combinations(self, valid_opportunity, base_form_data, data_updates, expected_valid):
        data = base_form_data.copy()
        data.update(data_updates)
        form = OpportunityChangeForm(data=data, instance=valid_opportunity)
        assert form.is_valid() == expected_valid

    def test_for_incomplete_opp(self, base_form_data, valid_opportunity):
        data = data = base_form_data.copy()
        PaymentUnit.objects.filter(opportunity=valid_opportunity).delete()  # making opp incomplete explicitly
        form = OpportunityChangeForm(data=data, instance=valid_opportunity)
        assert not form.is_valid()
        assert "users" in form.errors
        assert "Please finish setting up the opportunity before inviting users." in form.errors["users"]

    @override_switch(OPPORTUNITY_CREDENTIALS, active=True)
    @pytest.mark.parametrize(
        "learn_level,delivery_level",
        [
            ("LEARN_PASSED", "25_DELIVERIES"),
            ("LEARN_PASSED", "1000_DELIVERIES"),
            ("", "50_DELIVERIES"),
            ("LEARN_PASSED", ""),
            ("", ""),
        ],
    )
    def test_save_credential_issuer(self, valid_opportunity, base_form_data, learn_level, delivery_level):
        data = base_form_data.copy()
        data["learn_level"] = learn_level
        data["delivery_level"] = delivery_level

        form = OpportunityChangeForm(data=data, instance=valid_opportunity)
        assert form.is_valid(), form.errors
        form.save()

        if learn_level or delivery_level:
            credential_issuer = CredentialConfiguration.objects.get(opportunity=valid_opportunity)
            assert credential_issuer.learn_level == (learn_level or None)
            assert credential_issuer.delivery_level == (delivery_level or None)
        else:
            assert not CredentialConfiguration.objects.filter(opportunity=valid_opportunity).exists()

    @override_switch(OPPORTUNITY_CREDENTIALS, active=True)
    def test_invalid_credential_levels(self, valid_opportunity, base_form_data):
        data = base_form_data.copy()
        data["learn_level"] = "INVALID_LEVEL"
        data["delivery_level"] = "INVALID_DELIVERY"

        form = OpportunityChangeForm(data=data, instance=valid_opportunity)
        assert not form.is_valid()
        assert "learn_level" in form.errors or "delivery_level" in form.errors

    def test_credential_switch(self, valid_opportunity):
        form = OpportunityChangeForm(instance=valid_opportunity)
        assert "learn_level" not in form.fields
        assert "delivery_level" not in form.fields

        with override_switch(OPPORTUNITY_CREDENTIALS, active=True):
            form = OpportunityChangeForm(instance=valid_opportunity)
            assert "learn_level" in form.fields
            assert "delivery_level" in form.fields


class TestAddBudgetNewUsersForm:
    @pytest.fixture(
        params=[
            (5, 1, 2, 2, 200),  # amount, org_pay, max_total, total_user, program_budget
        ]
    )
    def setup(self, request, program_manager_org, organization):
        amount, org_pay, max_total, total_user, program_budget = request.param

        self.budget_per_user = (amount + org_pay) * max_total  # 12
        self.opp_total_budget_initially = total_user * self.budget_per_user  # 24

        self.program = ProgramFactory(organization=program_manager_org, budget=program_budget)
        self.opportunity = ManagedOpportunityFactory(
            program=self.program,
            organization=organization,
            total_budget=self.opp_total_budget_initially,
            org_pay_per_visit=org_pay,
            managed=True,
        )
        PaymentUnitFactory(opportunity=self.opportunity, max_total=max_total, amount=amount)

    @pytest.mark.parametrize("num_new_users, expected_budget", [(3, 60), (5, 84)])
    def test_valid_add_users(self, setup, num_new_users, expected_budget):
        form_data = {"add_users": num_new_users}
        form = AddBudgetNewUsersForm(data=form_data, opportunity=self.opportunity, program_manager=True)

        assert form.is_valid()
        form.save()
        self.opportunity.refresh_from_db()
        assert self.opportunity.total_budget == self.opp_total_budget_initially + (
            num_new_users * self.budget_per_user
        )

    @pytest.mark.parametrize("num_new_users", [200, 500])
    def test_exceeding_program_budget(self, setup, num_new_users):
        form_data = {"add_users": num_new_users}
        form = AddBudgetNewUsersForm(data=form_data, opportunity=self.opportunity, program_manager=True)

        assert not form.is_valid()
        assert "add_users" in form.errors
        assert form.errors["add_users"][0] == "Budget exceeds program budget."

    def test_missing_input(self, setup):
        form_data = {}
        form = AddBudgetNewUsersForm(data=form_data, opportunity=self.opportunity, program_manager=True)

        assert not form.is_valid()
        assert "Please provide either the number of users or a total budget." in form.errors["__all__"]

    def test_non_program_manager_access(self, setup):
        form_data = {"add_users": 2}
        form = AddBudgetNewUsersForm(data=form_data, opportunity=self.opportunity, program_manager=False)

        assert not form.is_valid()
        assert "__all__" in form.errors
        assert "Only program managers are allowed to add budgets for managed opportunities." in form.errors["__all__"]

    @pytest.mark.parametrize("new_budget, is_valid", [(150, True), (201, False)])
    def test_changing_total_budget(self, setup, new_budget, is_valid):
        form_data = {"total_budget": new_budget}
        form = AddBudgetNewUsersForm(data=form_data, opportunity=self.opportunity, program_manager=True)

        if is_valid:
            assert form.is_valid()
            form.save()
            self.opportunity.refresh_from_db()
            assert self.opportunity.total_budget == new_budget
        else:
            assert not form.is_valid()
            assert "total_budget" in form.errors
            assert form.errors["total_budget"][0] == "Total budget exceeds program budget."


@pytest.mark.django_db
class TestPaymentInvoiceForm:
    def test_form_initialization(self, valid_opportunity):
        form = PaymentInvoiceForm(opportunity=valid_opportunity)

        assert form.fields["invoice_number"].initial
        assert form.fields["date"].initial == str(datetime.date.today())
        assert form.fields["start_date"].initial == str(valid_opportunity.start_date)
        assert form.fields["end_date"].initial == str(datetime.date.today())

    def test_start_date_equal_to_first_uninvoiced_completed_work_date(self, valid_opportunity):
        cw = CompletedWorkFactory(
            status=CompletedWorkStatus.approved,
            opportunity_access__opportunity=valid_opportunity,
        )
        cw.date_created = datetime.date(2025, 10, 1)
        cw.save()

        form = PaymentInvoiceForm(opportunity=valid_opportunity)
        assert form.fields["start_date"].initial == str(datetime.date(2025, 10, 1))

        invoice = PaymentInvoiceFactory(opportunity=valid_opportunity)
        cw.invoice = invoice
        cw.save()

        cw = CompletedWorkFactory(
            status=CompletedWorkStatus.approved,
            opportunity_access__opportunity=valid_opportunity,
        )
        cw.date_created = datetime.date(2025, 10, 20)
        cw.save()

        form = PaymentInvoiceForm(opportunity=valid_opportunity)
        assert form.fields["start_date"].initial == str(datetime.date(2025, 10, 20))

    def test_duplicate_invoice_number(self, valid_opportunity):
        ExchangeRateFactory()
        PaymentInvoiceFactory(opportunity=valid_opportunity, invoice_number="INV-001")

        form = PaymentInvoiceForm(
            opportunity=valid_opportunity,
            data={
                "invoice_number": "INV-001",
                "date": "2025-11-06",
                "usd_currency": False,
                "local_amount": 100.0,
                "start_date": None,
                "end_date": None,
                "notes": "",
                "title": "",
                "invoice_type": "custom",
            },
        )
        assert not form.is_valid()
        assert form.errors["invoice_number"][0] == "Please use a different invoice number"

    def test_valid_form(self, valid_opportunity):
        ExchangeRateFactory()

        form = PaymentInvoiceForm(
            opportunity=valid_opportunity,
            data={
                "date": "2025-11-06",
                "amount": 100.0,
                "start_date": None,
                "end_date": None,
                "notes": "",
                "title": "",
                "invoice_type": "custom",
            },
        )

        assert form.is_valid()
        invoice = form.save()
        assert invoice.amount == 100.0
        assert invoice.date == datetime.date(2025, 11, 6)

    @patch("commcare_connect.opportunity.forms.link_invoice_to_completed_works")
    def test_non_service_delivery_form(self, mock_link_invoice, valid_opportunity):
        ExchangeRateFactory()

        form = PaymentInvoiceForm(
            opportunity=valid_opportunity,
            data={
                "invoice_number": "INV-001",
                "date": "2025-11-06",
                "amount": 100.0,
                "invoice_type": "custom",
                "title": "Consulting Services Invoice",
                "start_date": "2025-10-01",
                "end_date": "2025-10-31",
                "notes": "Monthly consulting services rendered.",
            },
        )
        assert form.is_valid()
        invoice = form.save()
        assert not invoice.service_delivery
        assert invoice.start_date is None
        assert invoice.end_date is None
        assert invoice.notes is None
        assert invoice.title is None

        mock_link_invoice.assert_not_called()

    @patch("commcare_connect.opportunity.forms.link_invoice_to_completed_works")
    def test_service_delivery_form(self, mock_link_invoice, valid_opportunity):
        ExchangeRateFactory()

        form = PaymentInvoiceForm(
            opportunity=valid_opportunity,
            data={
                "invoice_number": "INV-001",
                "amount": 100.0,
                "date": "2025-11-06",
                "invoice_type": "service_delivery",
                "title": "Consulting Services Invoice",
                "start_date": "2025-10-01",
                "end_date": "2025-10-31",
                "notes": "Monthly consulting services rendered.",
            },
        )
        assert form.is_valid()
        invoice = form.save()
        assert invoice.service_delivery
        assert str(invoice.start_date) == "2025-10-01"
        assert str(invoice.end_date) == "2025-10-31"
        assert invoice.notes == "Monthly consulting services rendered."

        mock_link_invoice.assert_called_once()

    def test_invoice_linkage(self, valid_opportunity):
        ExchangeRateFactory()
        cw = CompletedWorkFactory(
            opportunity_access__opportunity=valid_opportunity,
            status=CompletedWorkStatus.approved,
        )
        cw.date_created = datetime.date(2025, 10, 5)
        cw.save()

        assert CompletedWork.objects.get(id=cw.id).invoice is None

        form = PaymentInvoiceForm(
            opportunity=valid_opportunity,
            data={
                "invoice_number": "INV-001",
                "amount": 100.0,
                "date": "2025-11-06",
                "invoice_type": "service_delivery",
                "title": "Consulting Services Invoice",
                "start_date": "2025-10-01",
                "end_date": "2025-10-31",
                "notes": "Monthly consulting services rendered.",
            },
        )

        assert form.is_valid()
        invoice = form.save()

        assert CompletedWork.objects.get(id=cw.id).invoice == invoice
