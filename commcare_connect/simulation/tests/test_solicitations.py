import datetime

import pytest
from django.utils.timezone import now
from waffle.testutils import override_switch

from commcare_connect.flags.switch_names import SOLICITATIONS
from commcare_connect.simulation.steps import SolicitationSpec
from commcare_connect.simulation.steps.browser import StepRequestError
from commcare_connect.solicitation.models import Solicitation

pytestmark = pytest.mark.django_db


@pytest.fixture
def pm_org(sim, user):
    return sim.org("Sim PM", admin=user, program_manager=True)


@pytest.fixture(autouse=True)
def solicitations_switch():
    with override_switch(SOLICITATIONS, active=True):
        yield


def _in_days(days):
    return (now() + datetime.timedelta(days=days)).replace(minute=0, second=0, microsecond=0)


def test_solicitation_is_published_with_criteria_and_questions(sim, pm_org, user):
    spec = SolicitationSpec(
        title="Malaria outreach",
        deadline=_in_days(30),
        type=Solicitation.Type.EOI,
        country="KEN",
        delivery_type="Malaria",
    )

    solicitation = sim.solicitation(pm_org, spec)

    assert Solicitation.objects.open().filter(pk=solicitation.pk).exists()
    assert (solicitation.type, solicitation.public, solicitation.country_id) == (Solicitation.Type.EOI, True, "KEN")
    assert solicitation.delivery_type.name == "Malaria"
    assert (solicitation.organization, solicitation.created_by) == (pm_org, user.email)
    assert sum(criterion.weight for criterion in solicitation.criteria.all()) == 100
    assert all(question.criterion for question in solicitation.questions.all())


def test_unpublished_solicitation_stays_a_draft(sim, pm_org):
    solicitation = sim.solicitation(
        pm_org, SolicitationSpec(title="Draft", deadline=_in_days(30), public=False), publish=False
    )

    assert solicitation.status == Solicitation.Status.DRAFT
    assert not solicitation.public
    assert not Solicitation.objects.open().exists()


def test_solicitation_is_scheduled_to_go_live(sim, pm_org):
    go_live = _in_days(7)

    solicitation = sim.solicitation(
        pm_org, SolicitationSpec(title="Scheduled", deadline=_in_days(30)), go_live=go_live
    )

    assert (solicitation.status, solicitation.go_live_date) == (Solicitation.Status.SCHEDULED, go_live)
    assert not Solicitation.objects.open().exists()


def test_solicitation_built_in_the_past_has_expired_by_now(sim, pm_org):
    # Worked out before moving the clock, so it is five days before the real now.
    deadline = _in_days(-5)
    with sim.at(now() - datetime.timedelta(days=20)):
        solicitation = sim.solicitation(pm_org, SolicitationSpec(title="Expired", deadline=deadline))

    assert solicitation.status == Solicitation.Status.ACTIVE
    assert Solicitation.objects.expired().filter(pk=solicitation.pk).exists()


def test_refused_publish_creates_nothing(sim, pm_org):
    with pytest.raises(StepRequestError, match="deadline must be in the future"):
        sim.solicitation(pm_org, SolicitationSpec(title="Too late", deadline=_in_days(-1)))

    # The step is one transaction, so the draft the earlier pages saved goes too.
    assert not Solicitation.objects.exists()


def test_invite_to_private_solicitation_emails_the_org(sim, pm_org, user, mailoutbox):
    nm_org = sim.org("Sim NM", admin=user)
    solicitation = sim.solicitation(pm_org, SolicitationSpec(title="Private RFP", deadline=_in_days(30), public=False))

    invitations = sim.invite_to_solicitation(solicitation, [nm_org])

    assert [invitation.organization for invitation in invitations] == [nm_org]
    assert f"Invitation to apply: {solicitation.title}" in {message.subject for message in mailoutbox}


def test_switch_off_is_a_clear_error(sim, pm_org):
    with override_switch(SOLICITATIONS, active=False):
        with pytest.raises(RuntimeError, match="waffle_switch solicitations on"):
            sim.solicitation(pm_org, SolicitationSpec(title="Hidden", deadline=_in_days(30)))


def test_deadline_in_another_time_zone_is_posted_as_the_same_moment(sim, pm_org):
    deadline = _in_days(30)
    nairobi = datetime.timezone(datetime.timedelta(hours=3))

    solicitation = sim.solicitation(
        pm_org, SolicitationSpec(title="Nairobi time", deadline=deadline.astimezone(nairobi))
    )

    assert solicitation.application_deadline == deadline


def test_program_is_linked(sim, pm_org):
    program = sim.program(pm_org, name="Sim Program")

    solicitation = sim.solicitation(
        pm_org, SolicitationSpec(title="For a program", deadline=_in_days(30), program=program)
    )

    assert solicitation.program == program
