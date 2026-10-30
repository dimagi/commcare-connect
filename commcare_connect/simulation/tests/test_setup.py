import datetime

import pytest
from django.urls import NoReverseMatch
from waffle.testutils import override_switch

from commcare_connect.flags.switch_names import (
    AUTOMATIC_VISIT_VERIFICATION,
    ENABLE_PROGRAM_ACCESS_REDESIGN,
    OPTIONAL_DAILY_LIMIT,
)
from commcare_connect.opportunity.models import (
    DeliverUnit,
    DeliverUnitFlagRules,
    LearnModule,
    Opportunity,
    OpportunityVerificationFlags,
    PaymentUnit,
)
from commcare_connect.organization.models import Organization, UserOrganizationMembership
from commcare_connect.program.models import Program, ProgramApplication, ProgramApplicationStatus
from commcare_connect.simulation.constants import (
    DELIVER_UNIT_SLUGS,
    LEARN_MODULE_SLUGS,
    SIM_DELIVER_APP_PREFIX,
    SIM_DOMAIN,
    SIM_LEARN_APP_PREFIX,
)
from commcare_connect.simulation.steps import PaymentUnitSpec, Sim
from commcare_connect.simulation.steps.browser import StepRequestError
from commcare_connect.users.tests.factories import UserFactory
from commcare_connect.utils.permission_const import ORG_MANAGEMENT_SETTINGS_ACCESS

pytestmark = pytest.mark.django_db


@pytest.fixture
def joined(sim, user):
    """A program and an NM org that has joined it."""
    application = _join(sim, user)
    return application.program, application.organization


def test_org_makes_the_admin_a_member(sim, user):
    organization = sim.org("Sim Org", admin=user)

    assert organization.slug == "sim-org"
    assert not organization.program_manager
    membership = organization.memberships.get()
    assert (membership.user, membership.role) == (user, UserOrganizationMembership.Role.ADMIN)


def test_org_can_be_a_program_manager_without_the_admin_holding_the_permission(sim, user):
    assert not user.has_perm(ORG_MANAGEMENT_SETTINGS_ACCESS)

    organization = sim.org("Sim PM", admin=user, program_manager=True)

    organization.refresh_from_db()
    assert organization.program_manager
    # Enabled on Organization Home by a staff user who joins no organization.
    assert list(organization.memberships.values_list("user", flat=True)) == [user.pk]


def test_program_defaults_span_the_coming_months(sim, user):
    pm_org = sim.org("Sim PM", admin=user, program_manager=True)

    program = sim.program(pm_org, name="Sim Program")

    today = datetime.date.today()
    assert program.organization == pm_org
    assert program.start_date < today < program.end_date
    assert program.currency_id == "USD"
    assert program.created_by == user.email


def test_join_program_goes_through_invite_apply_and_accept(sim, user, mailoutbox):
    application = _join(sim, user)

    program = application.program
    application.refresh_from_db()
    assert application.status == ProgramApplicationStatus.ACCEPTED
    subjects = {message.subject for message in mailoutbox}
    assert f"Invitation to Program: {program.name}" in subjects
    assert f"Network Manager Applied for Program: {program.name}" in subjects


def test_opportunity_runs_the_setup_wizard(sim, joined):
    program, nm_org = joined

    opportunity = sim.opportunity(program, nm_org, name="Sim Opp", max_users=4)

    assert (opportunity.organization, opportunity.program, opportunity.managed) == (nm_org, program, True)
    assert opportunity.learn_app.cc_domain == opportunity.deliver_app.cc_domain == SIM_DOMAIN
    assert opportunity.learn_app.cc_app_id.startswith(SIM_LEARN_APP_PREFIX)
    assert opportunity.deliver_app.cc_app_id.startswith(SIM_DELIVER_APP_PREFIX)
    assert opportunity.learn_app.passing_score is not None
    assert _slugs(LearnModule, opportunity.learn_app) == set(LEARN_MODULE_SLUGS)
    assert _slugs(DeliverUnit, opportunity.deliver_app) == set(DELIVER_UNIT_SLUGS)

    payment_unit = PaymentUnit.objects.get(opportunity=opportunity)
    default = PaymentUnitSpec()
    assert (payment_unit.amount, payment_unit.max_total, payment_unit.max_daily) == (
        default.amount,
        default.max_total,
        default.max_daily,
    )
    assert _slugs(DeliverUnit, opportunity.deliver_app, payment_unit=payment_unit) == set(default.deliver_units)

    opportunity.refresh_from_db()
    assert opportunity.is_setup_complete
    assert opportunity.start_date == datetime.date.today()
    assert opportunity.total_budget == opportunity.budget_per_user() * 4


def test_each_opportunity_gets_its_own_apps(sim, joined):
    program, nm_org = joined

    first = sim.opportunity(program, nm_org, name="Sim Opp 1", finalize=False)
    second = sim.opportunity(program, nm_org, name="Sim Opp 2", finalize=False)

    assert first.learn_app != second.learn_app
    assert first.deliver_app.cc_app_id != second.deliver_app.cc_app_id
    assert not first.is_setup_complete


@override_switch(ENABLE_PROGRAM_ACCESS_REDESIGN, active=True)
def test_program_owner_supervises_under_the_access_redesign(sim, joined):
    program, nm_org = joined

    opportunity = sim.opportunity(program, nm_org, name="Sim Opp", finalize=False)

    assert opportunity.supervising_organization == program.organization


def test_opportunity_with_several_payment_units_and_flags(sim, joined):
    program, nm_org = joined
    units = [
        PaymentUnitSpec(name="Household", deliver_units=[DELIVER_UNIT_SLUGS[0]]),
        PaymentUnitSpec(name="Screening", max_daily=None, deliver_units=[DELIVER_UNIT_SLUGS[1]]),
    ]

    with override_switch(OPTIONAL_DAILY_LIMIT, active=True):
        opportunity = sim.opportunity(
            program,
            nm_org,
            name="Sim Opp",
            payment_units=units,
            verification_flags={"duplicate": True, "gps": True, "location": 25},
            deliver_unit_flags={DELIVER_UNIT_SLUGS[0]: {"duration": 2}},
        )

    assert set(PaymentUnit.objects.filter(opportunity=opportunity).values_list("name", "max_daily")) == {
        ("Household", 5),
        ("Screening", None),
    }
    flags = OpportunityVerificationFlags.objects.get(opportunity=opportunity)
    assert (flags.duplicate, flags.gps, flags.location) == (True, True, 25)
    rules = {
        rule.deliver_unit.slug: rule.duration for rule in DeliverUnitFlagRules.objects.filter(opportunity=opportunity)
    }
    # Only the first deliver unit is named, so the second one's rule form is submitted at its defaults (duration
    # 0, no attachment check). As on the page, an unchanged rule form saves no rule.
    assert _slugs(DeliverUnit, opportunity.deliver_app) == set(DELIVER_UNIT_SLUGS)
    assert rules == {DELIVER_UNIT_SLUGS[0]: 2}


def test_org_refused_for_a_taken_name_creates_nothing(sim, user):
    sim.org("Sim Org", admin=user)

    with pytest.raises(StepRequestError, match="already exists"):
        sim.org("sim org", admin=user)

    assert Organization.objects.filter(name__iexact="sim org").count() == 1


def test_program_refused_for_invalid_dates_creates_nothing(sim, user):
    pm_org = sim.org("Sim PM", admin=user, program_manager=True)

    with pytest.raises(StepRequestError, match="End date must be after the start date"):
        sim.program(pm_org, name="Backwards", start_date=datetime.date(2030, 1, 2), end_date=datetime.date(2030, 1, 1))

    assert not Program.objects.exists()


def test_join_refused_when_the_nm_admin_is_not_an_org_admin_leaves_no_application(sim, user):
    pm_org = sim.org("Sim PM", admin=user, program_manager=True)
    nm_org = sim.org("Sim NM", admin=user)
    program = sim.program(pm_org, name="Sim Program")
    outsider_nm = _sim_as(sim, pm_admin=user, nm_admin=UserFactory())

    with pytest.raises(StepRequestError, match="apply"):
        outsider_nm.join_program(program, nm_org)

    assert not ProgramApplication.objects.exists()


def test_opportunity_refused_for_a_non_member_creates_nothing(sim, user, joined):
    outsider_pm = _sim_as(sim, pm_admin=UserFactory(), nm_admin=user)

    with pytest.raises(StepRequestError, match="opportunity-init"):
        outsider_pm.opportunity(*joined, name="Sim Opp")

    assert not Opportunity.objects.exists()


def test_finalize_refused_over_the_program_budget_leaves_the_opportunity_unfinalized(sim, joined):
    program, nm_org = joined

    with pytest.raises(StepRequestError, match="Budget exceeds the program budget"):
        sim.opportunity(program, nm_org, name="Sim Opp", total_budget=program.budget + 1)

    opportunity = Opportunity.objects.get(name="Sim Opp")
    assert opportunity.total_budget is None
    assert opportunity.paymentunit_set.exists()


def test_finalize_refused_without_payment_units_leaves_the_opportunity_unfinalized(sim, joined):
    with pytest.raises(StepRequestError, match="finalize"):
        sim.opportunity(*joined, name="Sim Opp", payment_units=[])

    assert Opportunity.objects.get(name="Sim Opp").total_budget is None


def test_inviting_the_programs_own_org_is_refused(sim, joined):
    program, _nm_org = joined

    # The view refuses by redirecting to a URL name that doesn't exist, so the request fails before its message.
    with pytest.raises(NoReverseMatch, match="applications"):
        sim.join_program(program, program.organization)

    assert not ProgramApplication.objects.filter(organization=program.organization).exists()


def test_verification_refused_for_invalid_input_saves_no_rules(sim, joined):
    with pytest.raises(StepRequestError, match="greater than or equal to 0"):
        sim.opportunity(*joined, name="Sim Opp", verification_flags={"location": -1})

    opportunity = Opportunity.objects.get(name="Sim Opp")
    assert not OpportunityVerificationFlags.objects.filter(opportunity=opportunity).exists()


@override_switch(AUTOMATIC_VISIT_VERIFICATION, active=True)
def test_flags_the_page_hides_under_automatic_verification_are_an_error(sim, joined):
    with pytest.raises(ValueError, match="verifies visits automatically"):
        sim.opportunity(*joined, name="Sim Opp", verification_flags={"gps": True})


def test_separate_program_and_network_manager_admins(sim):
    pm_user, nm_user = UserFactory(), UserFactory()
    separate = _sim_as(sim, pm_admin=pm_user, nm_admin=nm_user)
    pm_org = separate.org("Sim PM", admin=pm_user, program_manager=True)
    nm_org = separate.org("Sim NM", admin=nm_user)
    program = separate.program(pm_org, name="Sim Program")

    application = separate.join_program(program, nm_org)
    opportunity = separate.opportunity(program, nm_org, name="Sim Opp", verification_flags={"gps": True})

    assert application.status == ProgramApplicationStatus.ACCEPTED
    assert application.modified_by == pm_user.email
    assert (program.created_by, opportunity.created_by) == (pm_user.email, pm_user.email)
    assert opportunity.organization == nm_org
    assert opportunity.is_setup_complete
    assert OpportunityVerificationFlags.objects.get(opportunity=opportunity).gps


def test_explicit_zero_budget_is_kept(sim, joined):
    opportunity = sim.opportunity(*joined, name="Sim Opp", total_budget=0)

    opportunity.refresh_from_db()
    assert opportunity.total_budget == 0
    assert not opportunity.is_setup_complete


@pytest.mark.parametrize(
    "options",
    [
        {"payment_units": [PaymentUnitSpec(deliver_units=["no_such_unit"])]},
        {"deliver_unit_flags": {"no_such_unit": {"duration": 2}}},
    ],
)
def test_unknown_deliver_unit_slug_is_an_error(sim, joined, options):
    with pytest.raises(ValueError, match="no_such_unit"):
        sim.opportunity(*joined, name="Sim Opp", **options)


def _sim_as(sim, *, pm_admin, nm_admin):
    """A ``Sim`` acting as other users, sharing ``sim``'s stand-ins and on_commit handling."""
    other = Sim(pm_admin=pm_admin, nm_admin=nm_admin, log=sim.log)
    other.commit_scope = sim.commit_scope
    return other


def _join(sim, user):
    """Build a PM org, its program and an NM org, and have the NM org join. Returns the application."""
    pm_org = sim.org("Sim PM", admin=user, program_manager=True)
    nm_org = sim.org("Sim NM", admin=user)
    return sim.join_program(sim.program(pm_org, name="Sim Program"), nm_org)


def _slugs(model, app, **filters):
    return set(model.objects.filter(app=app, **filters).values_list("slug", flat=True))
