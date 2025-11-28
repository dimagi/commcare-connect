import datetime
import json

import pytest
from waffle.testutils import override_switch

from commcare_connect.flags.switch_names import OPPORTUNITY_CREDENTIALS
from commcare_connect.opportunity.forms import AddBudgetNewUsersForm, OpportunityChangeForm, OpportunityInitUpdateForm
from commcare_connect.opportunity.models import CredentialConfiguration, PaymentUnit
from commcare_connect.opportunity.tests.factories import (
    CommCareAppFactory,
    OpportunityAccessFactory,
    OpportunityFactory,
    PaymentUnitFactory,
)
from commcare_connect.program.tests.factories import ManagedOpportunityFactory, ProgramFactory


@pytest.mark.django_db
class TestOpportunityChangeForm:
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
            "currency_fk": "EUR",
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
            "currency_fk",
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
            "currency_fk": valid_opportunity.currency_fk.code,
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
            "currency_fk",
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
            ({"currency_fk": "USD", "additional_users": 5}, True),
            ({"currency_fk": "EUR", "additional_users": 10}, True),
            ({"currency_fk": "INVALID", "additional_users": 5}, False),
            ({"currency_fk": "USD", "additional_users": -5}, True),
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


@pytest.mark.django_db
class TestOpportunityInitUpdateForm:
    @pytest.fixture
    def opportunity(self, organization):
        opportunity = OpportunityFactory(organization=organization)

        learn_app = CommCareAppFactory(
            organization=organization,
            cc_app_id="existing-learn-id",
            cc_domain="existing-learn-domain",
            name="Existing Learn App",
            description="Existing learn description",
            passing_score=65,
            hq_server=opportunity.hq_server,
        )
        deliver_app = CommCareAppFactory(
            organization=organization,
            cc_app_id="existing-deliver-id",
            cc_domain="existing-deliver-domain",
            name="Existing Deliver App",
            hq_server=opportunity.hq_server,
        )
        opportunity.learn_app = learn_app
        opportunity.deliver_app = deliver_app
        opportunity.save(update_fields=["learn_app", "deliver_app"])
        return opportunity

    def _build_form_data(
        self,
        opportunity,
        *,
        learn_payload,
        learn_domain,
        learn_description,
        learn_score,
        deliver_payload,
        deliver_domain,
        name="updated opportunity",
        currency_code="EUR",
        include_disabled_fields=True,
        hq_server=None,
    ):
        data = {
            "name": name,
            "description": "updated opportunity description",
            "short_description": "updated short description",
            "currency_fk": currency_code,
            "learn_app_description": learn_description,
            "learn_app_passing_score": learn_score,
        }
        if include_disabled_fields and learn_payload is not None and deliver_payload is not None:
            data.update(
                {
                    "hq_server": hq_server if hq_server is not None else opportunity.hq_server.id,
                    "api_key": str(opportunity.api_key.id),
                    "learn_app_domain": learn_domain,
                    "learn_app": json.dumps(learn_payload),
                    "deliver_app_domain": deliver_domain,
                    "deliver_app": json.dumps(deliver_payload),
                }
            )
        elif include_disabled_fields:
            data["hq_server"] = hq_server if hq_server is not None else opportunity.hq_server.id
        return data

    def _get_form(self, *, opportunity, data):
        return OpportunityInitUpdateForm(
            data=data,
            instance=opportunity,
            user=opportunity.api_key.user,
            org_slug=opportunity.organization.slug,
        )

    def test_updates_existing_linked_apps(self, opportunity):
        learn_app = opportunity.learn_app
        deliver_app = opportunity.deliver_app

        form_data = self._build_form_data(
            opportunity,
            learn_payload={"id": learn_app.cc_app_id, "name": "updated learn app"},
            learn_domain=learn_app.cc_domain,
            learn_description="updated learn description",
            learn_score=82,
            deliver_payload={"id": deliver_app.cc_app_id, "name": "updated deliver app"},
            deliver_domain=deliver_app.cc_domain,
        )

        form = self._get_form(opportunity=opportunity, data=form_data)
        assert form.is_valid(), form.errors

        updated_opportunity = form.save()
        updated_opportunity.refresh_from_db()
        learn_app.refresh_from_db()
        deliver_app.refresh_from_db()

        assert updated_opportunity.learn_app_id == learn_app.id
        assert learn_app.name == "updated learn app"
        assert learn_app.description == "updated learn description"
        assert learn_app.passing_score == 82

        assert updated_opportunity.deliver_app_id == deliver_app.id
        assert deliver_app.name == "updated deliver app"

        assert updated_opportunity.currency_fk.code == "EUR"

    def test_switching_to_new_apps_creates_fresh_records(self, opportunity):
        original_learn_app = opportunity.learn_app
        original_deliver_app = opportunity.deliver_app
        original_learn_name = original_learn_app.name
        original_deliver_name = original_deliver_app.name

        new_learn_payload = {"id": "new-learn-id", "name": "new learn app"}
        new_deliver_payload = {"id": "new-deliver-id", "name": "new deliver app"}

        form_data = self._build_form_data(
            opportunity,
            learn_payload=new_learn_payload,
            learn_domain="new-learn-domain",
            learn_description="new learn description",
            learn_score=90,
            deliver_payload=new_deliver_payload,
            deliver_domain="new-deliver-domain",
        )

        form = self._get_form(opportunity=opportunity, data=form_data)
        assert form.is_valid(), form.errors

        updated_opportunity = form.save()
        updated_opportunity.refresh_from_db()
        original_learn_app.refresh_from_db()
        original_deliver_app.refresh_from_db()

        assert original_learn_app.name == original_learn_name
        assert original_deliver_app.name == original_deliver_name

        assert updated_opportunity.learn_app.cc_app_id == new_learn_payload["id"]
        assert updated_opportunity.learn_app.cc_domain == "new-learn-domain"
        assert updated_opportunity.learn_app.name == "new learn app"
        assert updated_opportunity.learn_app.description == "new learn description"

        assert updated_opportunity.deliver_app.cc_app_id == new_deliver_payload["id"]
        assert updated_opportunity.deliver_app.cc_domain == "new-deliver-domain"
        assert updated_opportunity.deliver_app.name == "new deliver app"

        assert updated_opportunity.learn_app_id != original_learn_app.id
        assert updated_opportunity.deliver_app_id != original_deliver_app.id

    def test_disabled_fields_submission_errors(self, opportunity):
        learn_app = opportunity.learn_app
        deliver_app = opportunity.deliver_app
        OpportunityAccessFactory(opportunity=opportunity)

        form_data = self._build_form_data(
            opportunity,
            learn_payload={"id": "invalid learn-id", "name": "invalid Learn App"},
            learn_domain="invalid learn-domain",
            learn_description="updated learn description",
            learn_score=82,
            deliver_payload={"id": "invalid deliver-id", "name": "invalid Deliver App"},
            deliver_domain="invalid deliver-domain",
            hq_server=opportunity.hq_server.id,
        )

        form = self._get_form(opportunity=opportunity, data=form_data)
        assert not form.is_valid()
        assert "hq_server" in form.errors
        assert "api_key" in form.errors
        assert "learn_app" in form.errors
        assert "deliver_app" in form.errors

        learn_app.refresh_from_db()
        deliver_app.refresh_from_db()
        assert learn_app.cc_app_id != "invalid learn-id"
        assert deliver_app.cc_app_id != "invalid deliver-id"

    def test_updates_learn_details_when_fields_disabled(self, opportunity):
        OpportunityAccessFactory(opportunity=opportunity)
        learn_app = opportunity.learn_app

        form_data = self._build_form_data(
            opportunity,
            learn_payload=None,
            learn_domain=None,
            learn_description="updated learn description",
            learn_score=91,
            deliver_payload=None,
            deliver_domain=None,
            include_disabled_fields=False,
            name="updated opportunity",
        )

        form = self._get_form(opportunity=opportunity, data=form_data)
        assert form.is_valid(), form.errors

        updated_opportunity = form.save()
        learn_app.refresh_from_db()

        assert updated_opportunity.learn_app_id == learn_app.id
        assert learn_app.description == "updated learn description"
        assert learn_app.passing_score == 91


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
