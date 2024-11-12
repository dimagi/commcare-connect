import datetime
import json
import random

import pytest
from factory.fuzzy import FuzzyDate, FuzzyText

from commcare_connect.opportunity.forms import OpportunityChangeForm, OpportunityCreationForm
from commcare_connect.opportunity.tests.factories import ApplicationFactory, CommCareAppFactory, OpportunityFactory


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
        return OpportunityFactory(
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
            "additional_users": 5,
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
            "additional_users",
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
                    "field": "additional_users",
                    "value": "invalid",
                    "error_expected": True,
                    "error_message": "Enter a whole number.",
                },
                id="invalid_additional_users",
            ),
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
        OpportunityFactory(
            organization=organization,
            active=True,
            learn_app=CommCareAppFactory(cc_app_id=app_scenario["active_app_ids"][0]),
            deliver_app=CommCareAppFactory(cc_app_id=app_scenario["active_app_ids"][1]),
        )

        inactive_opp = OpportunityFactory(
            organization=organization,
            active=False,
            learn_app=CommCareAppFactory(cc_app_id=app_scenario["new_app_ids"][0]),
            deliver_app=CommCareAppFactory(cc_app_id=app_scenario["new_app_ids"][1]),
        )

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
