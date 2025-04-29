import datetime
import json
import random

import pytest
from factory.fuzzy import FuzzyDate, FuzzyText

from commcare_connect.opportunity.forms import AddBudgetNewUsersForm, OpportunityChangeForm, OpportunityCreationForm
from commcare_connect.opportunity.models import PaymentUnit
from commcare_connect.opportunity.tests.factories import (
    ApplicationFactory,
    CommCareAppFactory,
    OpportunityFactory,
    PaymentUnitFactory,
)
from commcare_connect.program.tests.factories import ManagedOpportunityFactory, ProgramFactory


class TestOpportunityCreationForm:
    learn_app = ApplicationFactory()
    deliver_app = ApplicationFactory()
    applications = [learn_app, deliver_app]

    def _get_opportunity(self):
        return {
            "name": "Test opportunity",
            "description": FuzzyText(length=150).fuzz(),
            "short_description": FuzzyText(length=50).fuzz(),
            "end_date": FuzzyDate(start_date=datetime.date.today()).fuzz(),
            "max_visits_per_user": 100,
            "daily_max_visits_per_user": 10,
            "total_budget": 100,
            "budget_per_visit": 10,
            "max_users": 10,
            "learn_app_domain": "test_domain",
            "learn_app": json.dumps(self.learn_app),
            "learn_app_description": FuzzyText(length=150).fuzz(),
            "learn_app_passing_score": random.randint(30, 100),
            "deliver_app_domain": "test_domain2",
            "deliver_app": json.dumps(self.deliver_app),
            "api_key": FuzzyText(length=36).fuzz(),
            "currency": FuzzyText(length=3).fuzz(),
        }

    def test_with_correct_data(self, organization):
        opportunity = self._get_opportunity()
        form = OpportunityCreationForm(
            opportunity, domains=["test_domain", "test_domain2"], org_slug=organization.slug
        )

        assert form.is_valid()
        assert len(form.errors) == 0

    def test_incorrect_end_date(self, organization):
        opportunity = self._get_opportunity()
        opportunity.update(
            end_date=datetime.date.today() - datetime.timedelta(days=20),
        )

        form = OpportunityCreationForm(
            opportunity, domains=["test_domain", "test_domain2"], org_slug=organization.slug
        )

        assert not form.is_valid()
        assert len(form.errors) == 1
        assert "end_date" in form.errors

    def test_same_learn_deliver_apps(self, organization):
        opportunity = self._get_opportunity()
        opportunity.update(
            deliver_app=json.dumps(self.learn_app),
        )

        form = OpportunityCreationForm(
            opportunity, domains=["test_domain", "test_domain2"], org_slug=organization.slug
        )

        assert not form.is_valid()
        assert len(form.errors) == 2
        assert "learn_app" in form.errors
        assert "deliver_app" in form.errors

    def test_daily_max_visits_greater_than_max_visits(self, organization):
        opportunity = self._get_opportunity()
        opportunity.update(
            daily_max_visits_per_user=1000,
            max_visits_per_user=100,
        )

        form = OpportunityCreationForm(
            opportunity, domains=["test_domain", "test_domain2"], org_slug=organization.slug
        )

        assert not form.is_valid()
        assert len(form.errors) == 1
        assert "daily_max_visits_per_user" in form.errors

    def test_budget_per_visit_greater_than_total_budget(self, organization):
        opportunity = self._get_opportunity()
        opportunity.update(
            budget_per_visit=1000,
            total_budget=100,
        )

        form = OpportunityCreationForm(
            opportunity, domains=["test_domain", "test_domain2"], org_slug=organization.slug
        )

        assert not form.is_valid()
        assert len(form.errors) == 1
        assert "budget_per_visit" in form.errors

    @pytest.mark.django_db
    def test_save(self, user, organization):
        opportunity = self._get_opportunity()
        form = OpportunityCreationForm(
            opportunity, domains=["test_domain", "test_domain2"], user=user, org_slug=organization.slug
        )
        form.is_valid()
        form.save()


@pytest.mark.django_db
class TestOpportunityChangeForm:
    @pytest.fixture(autouse=True)
    def setup_credentials_mock(self, monkeypatch):
        self.mock_credentials = [
            type("Credential", (), {"slug": "cert1", "name": "Work for test"}),
            type("Credential", (), {"slug": "cert2", "name": "Work for test"}),
        ]
        monkeypatch.setattr(
            "commcare_connect.connect_id_client.fetch_credentials", lambda org_slug: self.mock_credentials
        )

    @pytest.fixture
    def valid_opportunity(self, organization):
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
            "filter_country": "US",
            "filter_credential": "cert1",
        }

    def test_form_initialization(self, valid_opportunity, organization):
        form = OpportunityChangeForm(instance=valid_opportunity, org_slug=organization.slug)
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
            "filter_country",
            "filter_credential",
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
            "filter_country": [""],
            "filter_credential": [""],
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
    def test_required_fields(self, valid_opportunity, organization, field, base_form_data):
        data = base_form_data.copy()
        data[field] = ""
        form = OpportunityChangeForm(data=data, instance=valid_opportunity, org_slug=organization.slug)
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
    def test_field_validation(self, valid_opportunity, organization, base_form_data, test_data):
        data = base_form_data.copy()
        data[test_data["field"]] = test_data["value"]
        form = OpportunityChangeForm(data=data, instance=valid_opportunity, org_slug=organization.slug)
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

        form = OpportunityChangeForm(data=base_form_data, instance=inactive_opp, org_slug=organization.slug)

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
    def test_valid_combinations(self, valid_opportunity, organization, base_form_data, data_updates, expected_valid):
        data = base_form_data.copy()
        data.update(data_updates)
        form = OpportunityChangeForm(data=data, instance=valid_opportunity, org_slug=organization.slug)
        assert form.is_valid() == expected_valid

    def test_for_incomplete_opp(self, base_form_data, valid_opportunity, organization):
        data = data = base_form_data.copy()
        PaymentUnit.objects.filter(opportunity=valid_opportunity).delete()  # making opp incomplete explicitly
        form = OpportunityChangeForm(
            data=data,
            instance=valid_opportunity,
            org_slug=organization.slug,
        )
        assert not form.is_valid()
        assert "users" in form.errors
        assert "Please finish setting up the opportunity before inviting users." in form.errors["users"]


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
