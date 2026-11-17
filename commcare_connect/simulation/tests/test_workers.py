import datetime
from contextlib import nullcontext

import pytest
from django.utils.timezone import now

from commcare_connect.commcarehq.tests.factories import HQServerFactory
from commcare_connect.opportunity.models import (
    Assessment,
    CompletedModule,
    DeliverUnit,
    OpportunityAccess,
    OpportunityClaim,
    OpportunityClaimLimit,
    UserInvite,
    UserInviteStatus,
)
from commcare_connect.opportunity.tasks import sync_learn_modules_and_deliver_units
from commcare_connect.opportunity.tests.factories import (
    CommCareAppFactory,
    HQApiKeyFactory,
    OpportunityFactory,
    PaymentUnitFactory,
)
from commcare_connect.simulation.constants import (
    DELIVER_UNIT_SLUGS,
    LEARN_MODULE_SLUGS,
    SIM_DELIVER_APP_PREFIX,
    SIM_DOMAIN,
    SIM_LEARN_APP_PREFIX,
)
from commcare_connect.simulation.fakes.workers import connect_id_user
from commcare_connect.users.models import ConnectIDUserLink

pytestmark = pytest.mark.django_db


@pytest.fixture
def opportunity(sim, organization):
    """A set-up opportunity on the simulated apps; the setup steps own creating one through the UI."""
    hq_server = HQServerFactory()
    opportunity = OpportunityFactory(
        organization=organization,
        hq_server=hq_server,
        api_key=HQApiKeyFactory(hq_server=hq_server),
        learn_app=_app(organization, hq_server, SIM_LEARN_APP_PREFIX, passing_score=70),
        deliver_app=_app(organization, hq_server, SIM_DELIVER_APP_PREFIX),
        start_date=datetime.date.today() - datetime.timedelta(days=30),
        end_date=datetime.date.today() + datetime.timedelta(days=60),
        total_budget=10_000,
    )
    sync_learn_modules_and_deliver_units(opportunity)
    payment_unit = PaymentUnitFactory(opportunity=opportunity, amount=5, org_amount=1, max_total=20, max_daily=5)
    DeliverUnit.objects.filter(app=opportunity.deliver_app, slug=DELIVER_UNIT_SLUGS[0]).update(
        payment_unit=payment_unit
    )
    return opportunity


@pytest.fixture
def workers(sim, opportunity):
    return sim.invite(opportunity, 2)


def _app(organization, hq_server, prefix, **kwargs):
    return CommCareAppFactory(
        organization=organization, hq_server=hq_server, cc_domain=SIM_DOMAIN, cc_app_id=f"{prefix}1", **kwargs
    )


def test_invite_creates_accesses_and_sends_invites(sim, opportunity):
    workers = sim.invite(opportunity, 3)

    assert len({worker.pk for worker in workers}) == 3
    assert [worker.username for worker in workers] == [connect_id_user(w.phone_number).username for w in workers]
    invites = UserInvite.objects.filter(opportunity_access__in=_accesses(opportunity, workers))
    assert [invite.status for invite in invites] == [UserInviteStatus.invited] * 3
    assert all(invite.message_sid.startswith("SM") for invite in invites)


def test_invite_is_deterministic_for_a_seed(sim, opportunity):
    first = sim.invite(opportunity, 2)
    sim.rng.seed(42)

    assert sim.invite(opportunity, 2) == first


def test_claim_creates_claim_limits_and_hq_user_link(sim, opportunity, workers):
    sim.claim(opportunity, workers)

    assert OpportunityClaim.objects.filter(opportunity_access__user__in=workers).count() == len(workers)
    limits = OpportunityClaimLimit.objects.filter(opportunity_claim__opportunity_access__user__in=workers)
    assert [limit.max_visits for limit in limits] == [20, 20]
    links = ConnectIDUserLink.objects.filter(user__in=workers, domain=SIM_DOMAIN).select_related("user")
    assert {link.commcare_username for link in links} == {f"{w.username}@{SIM_DOMAIN}.commcarehq.org" for w in workers}
    assert all(link.hq_user_uuid for link in links)


def test_learn_completes_modules_and_scores_assessment(sim, opportunity, workers):
    sim.learn(opportunity, workers, score=80)

    for access in _accesses(opportunity, workers):
        assert (access.accepted, access.learn_progress) == (True, 100.0)
        assert access.completed_learn_date < datetime.datetime.now(datetime.UTC)
        assessment = Assessment.objects.get(opportunity_access=access)
        assert (assessment.score, assessment.passed) == (80, True)
        assert access.userinvite.status == UserInviteStatus.accepted


def test_partial_learn_skips_assessment(sim, opportunity):
    [worker] = sim.invite(opportunity, 1)

    sim.learn(opportunity, [worker], complete=0.4)

    completed = CompletedModule.objects.filter(user=worker).values_list("module__slug", flat=True)
    assert list(completed) == [LEARN_MODULE_SLUGS[0]]
    assert not Assessment.objects.filter(user=worker).exists()


def test_learn_then_claim_reuses_the_hq_user_link(sim, opportunity, workers):
    sim.learn(opportunity, workers)
    sim.claim(opportunity, workers)

    assert ConnectIDUserLink.objects.filter(user__in=workers).count() == len(workers)


@pytest.mark.parametrize("days_after_invite", [0, 10])
def test_recorded_dates_follow_the_simulated_clock(sim, opportunity, days_after_invite):
    invited_at = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=25)
    learn_at = invited_at + datetime.timedelta(days=days_after_invite)
    with sim.at(invited_at):
        [worker] = sim.invite(opportunity, 1)
        # Re-entering sim.at at the invite's own instant would rewind past it, so the same-moment case stays put.
        with sim.at(learn_at) if days_after_invite else nullcontext():
            sim.learn(opportunity, [worker])
            sim.claim(opportunity, [worker])
            learned_by = now()

    [access] = _accesses(opportunity, [worker])
    module_dates = sorted(CompletedModule.objects.filter(user=worker).values_list("date", flat=True))
    assessment_date = Assessment.objects.get(opportunity_access=access).date
    assert invited_at <= access.invited_date <= access.date_learn_started <= module_dates[0]
    assert module_dates[-1] == access.completed_learn_date <= assessment_date <= learned_by
    assert OpportunityClaim.objects.get(opportunity_access=access).date_claimed == learn_at.date()


def _accesses(opportunity, workers):
    return OpportunityAccess.objects.filter(opportunity=opportunity, user__in=workers)
