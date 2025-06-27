import json
import random

import pytest
from django.utils import timezone
from factory.fuzzy import FuzzyText

from commcare_connect.opportunity.forms import OpportunityFinalizeForm
from commcare_connect.opportunity.models import Opportunity
from commcare_connect.opportunity.tests.factories import (
    ApplicationFactory,
    DeliveryTypeFactory,
    HQApiKeyFactory,
    PaymentUnitFactory,
)
from commcare_connect.program.forms import ManagedOpportunityInitForm, ProgramForm
from commcare_connect.program.models import ManagedOpportunity, Program, ProgramApplicationStatus
from commcare_connect.program.tests.factories import (
    ManagedOpportunityFactory,
    ProgramApplicationFactory,
    ProgramFactory,
)
from commcare_connect.users.tests.factories import HQServerFactory, OrganizationFactory


@pytest.fixture
def delivery_type():
    return DeliveryTypeFactory()


@pytest.mark.django_db
class TestProgramForm:
    def _get_program_data(self, delivery_type):
        return {
            "name": "Test Program",
            "description": "This is a test description.",
            "delivery_type": delivery_type.id,
            "budget": 10000,
            "currency": "USD",
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
            currency="USDA",
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


@pytest.mark.django_db
class TestManagedOpportunityInitForm:
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
        form = ManagedOpportunityInitForm(program=self.program, org_slug=self.organization.slug)
        assert form.fields["currency"].initial == self.program.currency
        assert form.fields["currency"].widget.attrs.get("readonly") == "readonly"
        assert form.fields["currency"].widget.attrs.get("disabled") is True
        assert "organization" in form.fields

    def test_form_validation_valid_data(self):
        form = ManagedOpportunityInitForm(data=self.form_data, program=self.program, org_slug=self.organization.slug)
        print(form.errors)
        assert form.is_valid()

    def test_form_validation_invalid_data(self):
        invalid_data = self.form_data.copy()
        invalid_data["learn_app"] = invalid_data["deliver_app"]
        form = ManagedOpportunityInitForm(data=invalid_data, program=self.program, org_slug=self.organization.slug)
        assert not form.is_valid()
        assert form.errors["learn_app"] == ["Learn app and Deliver app cannot be same"]
        assert form.errors["deliver_app"] == ["Learn app and Deliver app cannot be same"]

    def test_form_validation_missing_data(self):
        invalid_data = self.form_data.copy()
        invalid_data["learn_app"] = None
        form = ManagedOpportunityInitForm(data=invalid_data, program=self.program, org_slug=self.organization.slug)
        assert not form.is_valid()

    def test_form_save(self):
        form = ManagedOpportunityInitForm(
            data=self.form_data,
            program=self.program,
            org_slug=self.organization.slug,
            user=self.user,
        )
        assert form.is_valid()
        form.save()
        assert ManagedOpportunity.objects.count() == 1
        managed_opportunity = ManagedOpportunity.objects.first()
        assert managed_opportunity.name == "Test managed opportunity"
        assert managed_opportunity.currency == self.program.currency
        assert managed_opportunity.program == self.program
        assert managed_opportunity.created_by == self.user.email


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
        manage_opp = ManagedOpportunityFactory.create(
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
            "org_pay_per_visit": 7,
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

    def test_form_invalid_org_pay_per_visit(self):
        self.opportunity.managed = True
        self.opportunity.save()
        form_data = {
            "start_date": timezone.now().date() + timezone.timedelta(days=2),
            "end_date": timezone.now().date() + timezone.timedelta(days=20),
            "total_budget": 5000,
            "org_pay_per_visit": "invalid",  # Invalid value
        }
        form = self.get_form(**form_data)
        assert not form.is_valid()
        assert "org_pay_per_visit" in form.errors

    def test_form_no_org_pay_per_visit_field(self):
        self.opportunity.managed = False
        self.opportunity.save()
        form = self.get_form(
            start_date=timezone.now().date() + timezone.timedelta(days=2),
            end_date=timezone.now().date() + timezone.timedelta(days=20),
            total_budget=5000,
        )
        assert "org_pay_per_visit" not in form.fields
