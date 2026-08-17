import json
import random

import pytest
from django.utils import timezone
from factory.fuzzy import FuzzyText
from waffle.testutils import override_switch

from commcare_connect.commcarehq.tests.factories import HQServerFactory
from commcare_connect.flags.switch_names import ENABLE_PROGRAM_ACCESS_REDESIGN
from commcare_connect.opportunity.forms import OpportunityFinalizeForm, OpportunityInitForm
from commcare_connect.opportunity.models import Opportunity
from commcare_connect.opportunity.tests.factories import (
    ApplicationFactory,
    DeliveryTypeFactory,
    HQApiKeyFactory,
    OpportunityFactory,
    PaymentUnitFactory,
)
from commcare_connect.program.forms import ProgramForm
from commcare_connect.program.helpers import eligible_funders, eligible_watchers
from commcare_connect.program.models import Program, ProgramApplicationStatus
from commcare_connect.program.tests.factories import ProgramApplicationFactory, ProgramFactory
from commcare_connect.users.tests.factories import OrganizationFactory


@pytest.fixture
def delivery_type():
    return DeliveryTypeFactory()


@pytest.fixture
def switch_enable_program_access_redesign_enabled():
    with override_switch(ENABLE_PROGRAM_ACCESS_REDESIGN, active=True):
        yield


@pytest.mark.django_db
class TestProgramForm:
    def _get_program_data(self, delivery_type):
        return {
            "name": "Test Program",
            "description": "This is a test description.",
            "delivery_type": delivery_type.id,
            "budget": 10000,
            "currency": "USD",
            "country": "USA",
            "start_date": timezone.now().date(),
            "end_date": timezone.now().date() + timezone.timedelta(days=30),
        }

    def test_program_form_valid_data(self, program_manager_org_user_admin, program_manager_org, delivery_type):
        program_data = self._get_program_data(delivery_type)
        form = ProgramForm(user=program_manager_org_user_admin, organization=program_manager_org, data=program_data)

        assert form.is_valid()
        assert len(form.errors) == 0

    def test_program_form_end_date_before_start_date(
        self, program_manager_org_user_admin, program_manager_org, delivery_type
    ):
        program_data = self._get_program_data(delivery_type)
        program_data.update(end_date=timezone.now().date() - timezone.timedelta(days=1))

        form = ProgramForm(user=program_manager_org_user_admin, organization=program_manager_org, data=program_data)

        assert not form.is_valid()
        assert len(form.errors) == 1
        assert "end_date" in form.errors

    def test_program_form_currency_length(self, program_manager_org_user_admin, program_manager_org, delivery_type):
        program_data = self._get_program_data(delivery_type)
        program_data.update(
            currency="INVALID",
        )

        form = ProgramForm(user=program_manager_org_user_admin, organization=program_manager_org, data=program_data)

        assert not form.is_valid()
        assert len(form.errors) == 1
        assert "currency" in form.errors

    @pytest.mark.django_db
    def test_program_form_save(self, program_manager_org_user_admin, program_manager_org, delivery_type):
        program_data = self._get_program_data(delivery_type)
        form = ProgramForm(user=program_manager_org_user_admin, organization=program_manager_org, data=program_data)

        assert form.is_valid()
        program = form.save()

        assert isinstance(program, Program)
        assert program.name == program_data["name"]
        assert program.organization == program_manager_org
        assert program.created_by == program_manager_org_user_admin.email
        assert program.modified_by == program_manager_org_user_admin.email
        assert program.currency.code == program_data["currency"]


@pytest.mark.django_db
class TestOpportunityInitForm:
    @pytest.fixture(autouse=True)
    def setup(self, program_manager_org, program_manager_org_user_admin):
        self.user = program_manager_org_user_admin
        self.organization = program_manager_org
        self.program = ProgramFactory.create(organization=program_manager_org)
        self.invited_org = OrganizationFactory.create()
        self.program_application = ProgramApplicationFactory.create(
            program=self.program, organization=self.invited_org, status=ProgramApplicationStatus.ACCEPTED
        )
        self.hq_server = HQServerFactory()
        self.api_key = HQApiKeyFactory(hq_server=self.hq_server)
        self.learn_app = ApplicationFactory()
        self.deliver_app = ApplicationFactory()

        self.form_data = {
            "name": "Test managed opportunity",
            "description": FuzzyText(length=150).fuzz(),
            "short_description": FuzzyText(length=50).fuzz(),
            "organization": self.invited_org.id,
            "learn_app_domain": "test_domain",
            "learn_app": json.dumps(self.learn_app),
            "learn_app_description": FuzzyText(length=150).fuzz(),
            "learn_app_passing_score": random.randint(30, 100),
            "deliver_app_domain": "test_domain2",
            "deliver_app": json.dumps(self.deliver_app),
            "api_key": self.api_key.id,
            "hq_server": self.hq_server.id,
        }

    def test_form_initialization(self):
        form = OpportunityInitForm(program=self.program, org_slug=self.organization.slug)
        assert form.fields["currency"].initial == self.program.currency
        assert form.fields["currency"].widget.attrs.get("readonly") == "readonly"
        assert form.fields["currency"].widget.attrs.get("disabled") is True
        assert "organization" in form.fields

    def test_form_validation_valid_data(self):
        form = OpportunityInitForm(data=self.form_data, program=self.program, org_slug=self.organization.slug)
        assert form.is_valid()

    def test_form_validation_invalid_data(self):
        invalid_data = self.form_data.copy()
        invalid_data["learn_app"] = invalid_data["deliver_app"]
        form = OpportunityInitForm(data=invalid_data, program=self.program, org_slug=self.organization.slug)
        assert not form.is_valid()
        assert form.errors["learn_app"] == ["Learn app and Deliver app cannot be same"]
        assert form.errors["deliver_app"] == ["Learn app and Deliver app cannot be same"]

    def test_form_validation_missing_data(self):
        invalid_data = self.form_data.copy()
        invalid_data["learn_app"] = None
        form = OpportunityInitForm(data=invalid_data, program=self.program, org_slug=self.organization.slug)
        assert not form.is_valid()

    def test_form_save(self):
        form = OpportunityInitForm(
            data=self.form_data,
            program=self.program,
            org_slug=self.organization.slug,
            user=self.user,
        )
        assert form.is_valid()
        form.save()
        assert Opportunity.objects.count() == 1
        managed_opportunity = Opportunity.objects.first()
        assert managed_opportunity.name == "Test managed opportunity"
        assert managed_opportunity.currency == self.program.currency
        assert managed_opportunity.program == self.program
        assert managed_opportunity.created_by == self.user.email
        assert managed_opportunity.delivery_type == self.program.delivery_type


@pytest.mark.django_db
class TestOpportunityFinalizeForm:
    @pytest.fixture(autouse=True)
    def setup(self, program_manager_org, program_manager_org_user_admin):
        self.program = ProgramFactory.create(
            budget=10000,
            start_date=timezone.now().date(),
            end_date=timezone.now().date() + timezone.timedelta(days=30),
            organization=program_manager_org,
        )
        manage_opp = OpportunityFactory.create(
            program=self.program, start_date=timezone.now().date(), end_date=None, total_budget=None
        )
        self.opportunity = Opportunity.objects.get(id=manage_opp.id)
        self.payment_unit = PaymentUnitFactory.create(opportunity=self.opportunity, amount=50, max_total=20)

    def get_form(self, **kwargs):
        return OpportunityFinalizeForm(
            data=kwargs,
            budget_per_user=self.payment_unit.amount * self.payment_unit.max_total,
            payment_units_max_total=self.payment_unit.max_total,
            opportunity=self.opportunity,
            current_start_date=self.opportunity.start_date,
        )

    def test_form_valid(self):
        form_data = {
            "start_date": timezone.now().date() + timezone.timedelta(days=2),
            "end_date": timezone.now().date() + timezone.timedelta(days=20),
            "total_budget": 5000,
            "max_users": 3,
        }
        form = self.get_form(**form_data)
        assert form.is_valid()

    def test_form_invalid_dates(self):
        form_data = {
            "start_date": timezone.now().date() + timezone.timedelta(days=2),
            "end_date": timezone.now().date() - timezone.timedelta(days=20),  # Invalid: End date is before start date
            "total_budget": 5000,
        }
        form = self.get_form(**form_data)
        assert not form.is_valid()
        assert "end_date" in form.errors

    def test_form_invalid_end_date(self):
        form_data = {
            "start_date": timezone.now().date() + timezone.timedelta(days=2),
            "end_date": timezone.now().date() + timezone.timedelta(days=40),  # Invalid: End date is in the past
            "total_budget": 5000,
        }
        form = self.get_form(**form_data)
        assert not form.is_valid()
        assert "end_date" in form.errors

    def test_form_start_date_readonly(self):
        self.opportunity.start_date = timezone.now().date() - timezone.timedelta(days=10)
        self.opportunity.save()
        form = self.get_form(
            start_date=timezone.now().date() + timezone.timedelta(days=2),
            end_date=timezone.now().date() + timezone.timedelta(days=20),
            total_budget=5000,
        )
        assert form.fields["start_date"].disabled

    def test_form_budget_exceeds_program_budget(self):
        form_data = {
            "start_date": timezone.now().date() + timezone.timedelta(days=2),
            "end_date": timezone.now().date() + timezone.timedelta(days=20),
            "total_budget": 15000,  # Exceeds program budget
        }
        form = self.get_form(**form_data)
        assert not form.is_valid()
        assert form.errors["total_budget"] == ["Budget exceeds the program budget."]

    def test_form_no_org_pay_per_visit_field(self):
        self.opportunity.managed = False
        self.opportunity.save()
        form = self.get_form(
            start_date=timezone.now().date() + timezone.timedelta(days=2),
            end_date=timezone.now().date() + timezone.timedelta(days=20),
            total_budget=5000,
        )
        assert "org_pay_per_visit" not in form.fields


@pytest.mark.django_db
class TestFunderOrganizations:
    def test_includes_only_funders(self, program_manager_org, funder_org):
        non_funder = OrganizationFactory()

        result = eligible_funders(program_manager_org)

        assert funder_org in result
        assert non_funder not in result

    def test_excludes_own_organization(self, program_manager_org):
        program_manager_org.funder = True
        program_manager_org.save()

        assert program_manager_org not in eligible_funders(program_manager_org)


def program_form_data(delivery_type, **overrides):
    data = {
        "name": "Test Program",
        "description": "This is a test description.",
        "delivery_type": delivery_type.id,
        "budget": 10000,
        "currency": "USD",
        "country": "USA",
        "start_date": timezone.now().date(),
        "end_date": timezone.now().date() + timezone.timedelta(days=30),
    }
    data.update(overrides)
    return data


@pytest.mark.django_db
@pytest.mark.usefixtures("switch_enable_program_access_redesign_enabled")
class TestProgramFormFunderEnabled:
    def test_field_is_present(self, program_manager_org_user_admin, program_manager_org):
        form = ProgramForm(user=program_manager_org_user_admin, organization=program_manager_org)

        assert "funder" in form.fields

    def test_non_funder_organization_is_rejected(
        self, program_manager_org_user_admin, program_manager_org, delivery_type
    ):
        non_funder = OrganizationFactory()
        form = ProgramForm(
            user=program_manager_org_user_admin,
            organization=program_manager_org,
            data=program_form_data(delivery_type, funder=non_funder.id),
        )

        assert not form.is_valid()
        assert "funder" in form.errors

    def test_save_persists_funder(
        self, program_manager_org_user_admin, program_manager_org, delivery_type, funder_org
    ):
        form = ProgramForm(
            user=program_manager_org_user_admin,
            organization=program_manager_org,
            data=program_form_data(delivery_type, funder=funder_org.id),
        )

        assert form.is_valid(), form.errors
        program = form.save()

        assert program.funder == funder_org

    def test_save_without_funder(self, program_manager_org_user_admin, program_manager_org, delivery_type):
        form = ProgramForm(
            user=program_manager_org_user_admin,
            organization=program_manager_org,
            data=program_form_data(delivery_type),
        )

        assert form.is_valid(), form.errors

        assert form.save().funder is None


@pytest.mark.django_db
@pytest.mark.usefixtures("switch_enable_program_access_redesign_enabled")
class TestProgramFormFunderLockedOnEdit:
    @pytest.fixture
    def funded_program(self, program, funder_org):
        program.funder = funder_org
        program.save()
        return program

    def _edit_data(self, program, **overrides):
        data = {
            "name": program.name,
            "description": program.description,
            "delivery_type": program.delivery_type_id,
            "budget": program.budget,
            "currency": program.currency.code,
            "country": program.country.code,
            "start_date": program.start_date,
            "end_date": program.end_date,
        }
        data.update(overrides)
        return data

    def test_posted_funder_is_ignored(self, program_manager_org_user_admin, funded_program, funder_org):
        other_funder = OrganizationFactory(funder=True)
        form = ProgramForm(
            user=program_manager_org_user_admin,
            organization=funded_program.organization,
            instance=funded_program,
            data=self._edit_data(funded_program, funder=other_funder.id),
        )

        assert form.is_valid(), form.errors
        program = form.save()

        assert program.funder == funder_org


@pytest.mark.django_db
class TestProgramFormFunderDisabled:
    def test_field_is_absent_on_create(self, program_manager_org_user_admin, program_manager_org):
        form = ProgramForm(user=program_manager_org_user_admin, organization=program_manager_org)

        assert "funder" not in form.fields

    def test_posted_funder_is_ignored_on_create(
        self, program_manager_org_user_admin, program_manager_org, delivery_type, funder_org
    ):
        form = ProgramForm(
            user=program_manager_org_user_admin,
            organization=program_manager_org,
            data=program_form_data(delivery_type, funder=funder_org.id),
        )

        assert form.is_valid(), form.errors

        assert form.save().funder is None

    def test_existing_funder_survives_a_save(self, program_manager_org_user_admin, program, funder_org, delivery_type):
        program.funder = funder_org
        program.save()

        form = ProgramForm(
            user=program_manager_org_user_admin,
            organization=program.organization,
            instance=program,
            data=program_form_data(delivery_type, name="Renamed Program"),
        )

        assert form.is_valid(), form.errors
        updated = form.save()

        assert updated.name == "Renamed Program"
        assert updated.funder == funder_org


@pytest.mark.django_db
class TestWatcherOrganizations:
    def test_excludes_own_organization_and_funder(self, program_manager_org, funder_org, watcher_org):
        result = eligible_watchers(program_manager_org, funder_org)

        assert watcher_org in result
        assert program_manager_org not in result
        assert funder_org not in result


@pytest.mark.django_db
@pytest.mark.usefixtures("switch_enable_program_access_redesign_enabled")
class TestProgramFormWatchersEnabled:
    def test_field_is_present(self, program_manager_org_user_admin, program_manager_org):
        form = ProgramForm(user=program_manager_org_user_admin, organization=program_manager_org)

        assert "watchers" in form.fields

    def test_save_persists_watchers(
        self, program_manager_org_user_admin, program_manager_org, delivery_type, watcher_org
    ):
        form = ProgramForm(
            user=program_manager_org_user_admin,
            organization=program_manager_org,
            data=program_form_data(delivery_type, watchers=[watcher_org.id]),
        )

        assert form.is_valid(), form.errors

        assert list(form.save().watchers.all()) == [watcher_org]

    def test_save_without_watchers(self, program_manager_org_user_admin, program_manager_org, delivery_type):
        form = ProgramForm(
            user=program_manager_org_user_admin,
            organization=program_manager_org,
            data=program_form_data(delivery_type),
        )

        assert form.is_valid(), form.errors

        assert not form.save().watchers.exists()

    def test_own_organization_is_rejected_as_watcher(
        self, program_manager_org_user_admin, program_manager_org, delivery_type
    ):
        form = ProgramForm(
            user=program_manager_org_user_admin,
            organization=program_manager_org,
            data=program_form_data(delivery_type, watchers=[program_manager_org.id]),
        )

        assert not form.is_valid()
        assert "watchers" in form.errors

    def test_funder_cannot_also_be_a_watcher(
        self, program_manager_org_user_admin, program_manager_org, delivery_type, funder_org
    ):
        form = ProgramForm(
            user=program_manager_org_user_admin,
            organization=program_manager_org,
            data=program_form_data(delivery_type, funder=funder_org.id, watchers=[funder_org.id]),
        )

        assert not form.is_valid()
        assert form.errors["watchers"] == ["An organization cannot be both the funder and a watcher."]


@pytest.mark.django_db
@pytest.mark.usefixtures("switch_enable_program_access_redesign_enabled")
class TestProgramFormWatchersOnEdit:
    @pytest.fixture
    def watched_program(self, program, watcher_org):
        program.watchers.set([watcher_org])
        return program

    def _edit_data(self, program, **overrides):
        data = {
            "name": program.name,
            "description": program.description,
            "delivery_type": program.delivery_type_id,
            "budget": program.budget,
            "currency": program.currency.code,
            "country": program.country.code,
            "start_date": program.start_date,
            "end_date": program.end_date,
        }
        data.update(overrides)
        return data

    def test_watchers_can_be_changed(self, program_manager_org_user_admin, watched_program):
        new_watcher = OrganizationFactory()
        form = ProgramForm(
            user=program_manager_org_user_admin,
            organization=watched_program.organization,
            instance=watched_program,
            data=self._edit_data(watched_program, watchers=[new_watcher.id]),
        )

        assert form.is_valid(), form.errors

        assert list(form.save().watchers.all()) == [new_watcher]

    def test_current_funder_is_rejected_as_watcher(self, program_manager_org_user_admin, watched_program, funder_org):
        watched_program.funder = funder_org
        watched_program.save()

        form = ProgramForm(
            user=program_manager_org_user_admin,
            organization=watched_program.organization,
            instance=watched_program,
            data=self._edit_data(watched_program, watchers=[funder_org.id]),
        )

        assert not form.is_valid()
        assert "watchers" in form.errors


@pytest.mark.django_db
class TestProgramFormWatchersDisabled:
    def test_field_is_absent_on_create(self, program_manager_org_user_admin, program_manager_org):
        form = ProgramForm(user=program_manager_org_user_admin, organization=program_manager_org)

        assert "watchers" not in form.fields

    def test_posted_watchers_are_ignored_on_create(
        self, program_manager_org_user_admin, program_manager_org, delivery_type, watcher_org
    ):
        form = ProgramForm(
            user=program_manager_org_user_admin,
            organization=program_manager_org,
            data=program_form_data(delivery_type, watchers=[watcher_org.id]),
        )

        assert form.is_valid(), form.errors

        assert not form.save().watchers.exists()

    def test_existing_watchers_survive_a_save(
        self, program_manager_org_user_admin, program, watcher_org, delivery_type
    ):
        program.watchers.set([watcher_org])

        form = ProgramForm(
            user=program_manager_org_user_admin,
            organization=program.organization,
            instance=program,
            data=program_form_data(delivery_type, name="Renamed Program"),
        )

        assert form.is_valid(), form.errors
        updated = form.save()

        assert updated.name == "Renamed Program"
        assert list(updated.watchers.all()) == [watcher_org]
