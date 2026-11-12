import json
import random

import pytest
from crispy_forms.utils import render_crispy_form
from django.utils import timezone
from factory.fuzzy import FuzzyText
from waffle.testutils import override_switch

from commcare_connect.commcarehq.tests.factories import HQServerFactory
from commcare_connect.flags.switch_names import ENABLE_PROGRAM_ACCESS_REDESIGN
from commcare_connect.opportunity.forms import OpportunityFinalizeForm, OpportunityInitForm
from commcare_connect.opportunity.models import Opportunity, VisitValidationStatus
from commcare_connect.opportunity.tests.factories import (
    ApplicationFactory,
    DeliveryTypeFactory,
    HQApiKeyFactory,
    OpportunityFactory,
    PaymentUnitFactory,
    UserVisitFactory,
)
from commcare_connect.program.forms import ProgramForm
from commcare_connect.program.helpers import eligible_funders, eligible_watchers
from commcare_connect.program.models import (
    Program,
    ProgramApplicationStatus,
    ProgramFunderEvent,
    ProgramWatcherEvent,
)
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
        self.api_key = HQApiKeyFactory(hq_server=self.hq_server, user=self.user)
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

    def _form(self, data):
        return OpportunityInitForm(data=data, program=self.program, org_slug=self.organization.slug, user=self.user)

    def test_form_initialization(self):
        form = OpportunityInitForm(program=self.program, org_slug=self.organization.slug)
        assert form.fields["currency"].initial == self.program.currency
        assert form.fields["currency"].widget.attrs.get("readonly") == "readonly"
        assert form.fields["currency"].widget.attrs.get("disabled") is True
        assert "organization" in form.fields

    def test_form_validation_valid_data(self):
        form = self._form(self.form_data)
        assert form.is_valid()

    def test_form_validation_invalid_data(self):
        invalid_data = self.form_data.copy()
        invalid_data["learn_app"] = invalid_data["deliver_app"]
        form = self._form(invalid_data)
        assert not form.is_valid()
        assert form.errors["learn_app"] == ["Learn app and Deliver app cannot be same"]
        assert form.errors["deliver_app"] == ["Learn app and Deliver app cannot be same"]

    def test_form_validation_missing_data(self):
        invalid_data = self.form_data.copy()
        invalid_data["learn_app"] = None
        form = self._form(invalid_data)
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
            instance=self.opportunity,
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

    @pytest.mark.parametrize(
        "end_date_offset_days",
        [
            pytest.param(-20, id="before_start_date"),
            pytest.param(40, id="after_program_end_date"),
        ],
    )
    def test_form_invalid_end_date(self, end_date_offset_days):
        form_data = {
            "start_date": timezone.now().date() + timezone.timedelta(days=2),
            "end_date": timezone.now().date() + timezone.timedelta(days=end_date_offset_days),
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

    def test_max_users_oninput_uses_budget_per_user(self):
        PaymentUnitFactory.create(opportunity=self.opportunity, amount=3, org_amount=2, max_total=4)
        form = self.get_form()

        # (50 + org pay) * 20 for the setup unit, plus (3 + 2) * 4
        expected = (50 + self.payment_unit.org_amount) * 20 + 20
        assert f"id_total_budget.value = ({expected} * parseInt(this.value || 0))" in render_crispy_form(form)

    @pytest.mark.parametrize(
        "current_start, has_visit, expected_locked, expected_start_min, expected_help_text",
        [
            (-1, False, True, None, "can't be changed"),
            (0, True, True, None, "can't be changed"),
            (0, False, False, 0, ""),
            (3, True, False, 1, "Can't be today"),
        ],
    )
    def test_start_date_locked(
        self, current_start, has_visit, expected_locked, expected_start_min, expected_help_text
    ):
        self.opportunity.start_date = _days_from_today(current_start)
        self.opportunity.save()
        if has_visit:
            UserVisitFactory(
                opportunity=self.opportunity, status=VisitValidationStatus.approved, date_created=timezone.now()
            )

        form = self.get_form()
        start_field = form.fields["start_date"]
        assert start_field.disabled is expected_locked
        if expected_help_text:
            assert expected_help_text in start_field.help_text
        else:
            assert not start_field.help_text
        start_min = None if expected_start_min is None else str(_days_from_today(expected_start_min))
        assert start_field.widget.attrs.get("min") == start_min
        assert form.fields["end_date"].widget.attrs["min"] == str(_days_from_today(0))

    @pytest.mark.parametrize("visit_days_ago, valid", [(None, True), (0, False), (1, True)])
    def test_start_date_today_needs_no_visits_today(self, visit_days_ago, valid):
        self.opportunity.start_date = _days_from_today(3)
        self.opportunity.save()
        if visit_days_ago is not None:
            UserVisitFactory(
                opportunity=self.opportunity,
                status=VisitValidationStatus.trial,
                completed_work=None,
                date_created=timezone.now() - timezone.timedelta(days=visit_days_ago),
            )

        form = self.get_form(
            start_date=_days_from_today(0), end_date=_days_from_today(20), total_budget=5000, max_users=3
        )

        assert form.is_valid() is valid, form.errors
        if not valid:
            assert form.errors["start_date"] == [
                "Start date can be today only if the opportunity has no visits for today."
            ]

    def test_saved_start_date_stays_in_range(self):
        # Starts today with a trial visit from today: not locked, and the browser must still accept today
        UserVisitFactory(
            opportunity=self.opportunity,
            status=VisitValidationStatus.trial,
            completed_work=None,
            date_created=timezone.now(),
        )

        form = self.get_form()

        assert not form.fields["start_date"].disabled
        assert form.fields["start_date"].widget.attrs["min"] == str(_days_from_today(0))

    def test_trial_visit_does_not_lock_start_date(self):
        UserVisitFactory(opportunity=self.opportunity, status=VisitValidationStatus.trial, completed_work=None)

        assert not self.get_form().fields["start_date"].disabled

    @pytest.mark.parametrize(
        "unit_dates, start, end, error_field, error, error_date",
        [
            ((5, 15), 6, 20, "start_date", "after the start date of payment unit", 5),
            ((None, 5), 6, 20, "start_date", "after the end date of payment unit", 5),
            ((5, 15), 2, 14, "end_date", "before the end date of payment unit", 15),
            ((10, None), 2, 8, "end_date", "before the start date of payment unit", 10),
            ((5, 15), 5, 15, None, None, None),
            ((None, None), 6, 14, None, None, None),
        ],
    )
    def test_dates_cover_payment_units(self, unit_dates, start, end, error_field, error, error_date):
        self.payment_unit.start_date, self.payment_unit.end_date = map(_days_from_today, unit_dates)
        self.payment_unit.save()

        form = self.get_form(
            start_date=_days_from_today(start), end_date=_days_from_today(end), total_budget=5000, max_users=3
        )

        assert form.is_valid() is (error_field is None), form.errors
        if error_field:
            message = f"{error} {self.payment_unit.name} ({_days_from_today(error_date)})."
            assert message in form.errors[error_field][0]

    @pytest.mark.parametrize(
        "unit_dates, expected_start_max, expected_end_min",
        [
            ((5, 15), 5, 15),
            ((None, 5), 5, 5),
            ((10, None), 10, 10),
            ((None, None), None, 0),  # no unit dates: only today limits the end
        ],
    )
    def test_date_pickers_cover_payment_units(self, unit_dates, expected_start_max, expected_end_min):
        self.payment_unit.start_date, self.payment_unit.end_date = map(_days_from_today, unit_dates)
        self.payment_unit.save()

        form = self.get_form()

        start_field, end_field = form.fields["start_date"], form.fields["end_date"]
        assert end_field.widget.attrs["min"] == str(_days_from_today(expected_end_min))
        if expected_start_max is None:
            assert "max" not in start_field.widget.attrs
            assert not start_field.help_text and not end_field.help_text
        else:
            assert start_field.widget.attrs["max"] == str(_days_from_today(expected_start_max))
            assert "on or before" in start_field.help_text
            assert "on or after" in end_field.help_text


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


def _days_from_today(days):
    return None if days is None else timezone.now().date() + timezone.timedelta(days=days)


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


def program_edit_data(program, **overrides):
    """Form data that matches an existing program, with optional overrides."""
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

    def test_posted_funder_is_ignored(self, program_manager_org_user_admin, funded_program, funder_org):
        other_funder = OrganizationFactory(funder=True)
        form = ProgramForm(
            user=program_manager_org_user_admin,
            organization=funded_program.organization,
            instance=funded_program,
            data=program_edit_data(funded_program, funder=other_funder.id),
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

    def test_watchers_can_be_changed(self, program_manager_org_user_admin, watched_program):
        new_watcher = OrganizationFactory()
        form = ProgramForm(
            user=program_manager_org_user_admin,
            organization=watched_program.organization,
            instance=watched_program,
            data=program_edit_data(watched_program, watchers=[new_watcher.id]),
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
            data=program_edit_data(watched_program, watchers=[funder_org.id]),
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


@pytest.mark.django_db
class TestProgramAccessAudit:
    """The audit trail is installed as database triggers, so it records changes made from
    anywhere, not only through the form or while the access redesign switch is on."""

    def test_funder_assignment_and_change_are_recorded(self, program, funder_org):
        other_funder = OrganizationFactory(funder=True)
        program.funder = funder_org
        program.save()
        program.funder = other_funder
        program.save()

        events = ProgramFunderEvent.objects.filter(pgh_obj=program).order_by("pgh_id")

        assert [event.funder_id for event in events] == [None, funder_org.id, other_funder.id]

    def test_unrelated_program_edit_records_nothing(self, program):
        starting_count = ProgramFunderEvent.objects.filter(pgh_obj=program).count()

        program.name = "Renamed Program"
        program.save()

        assert ProgramFunderEvent.objects.filter(pgh_obj=program).count() == starting_count

    def test_adding_and_removing_a_watcher_are_recorded(self, program, watcher_org):
        program.watchers.add(watcher_org)
        program.watchers.remove(watcher_org)

        events = ProgramWatcherEvent.objects.filter(program=program, organization=watcher_org).order_by("pgh_id")

        assert [event.pgh_label for event in events] == ["insert", "delete"]
