import datetime
from collections import Counter

import pytest
from django.core.exceptions import PermissionDenied
from django.utils.timezone import now

from commcare_connect.opportunity.models import (
    CompletedWork,
    DeliverUnitFlagRules,
    OpportunityAccess,
    OpportunityClaimLimit,
    UserVisit,
    VisitReviewStatus,
    VisitValidationStatus,
)
from commcare_connect.opportunity.tests.factories import (
    CommCareAppFactory,
    DeliverUnitFactory,
    DeliverUnitFlagRulesFactory,
    HQApiKeyFactory,
    OpportunityAccessFactory,
    OpportunityClaimFactory,
    OpportunityFactory,
    OpportunityVerificationFlagsFactory,
    PaymentUnitFactory,
)
from commcare_connect.program.tests.factories import ProgramFactory
from commcare_connect.simulation.constants import (
    DELIVER_UNIT_SLUGS,
    SIM_DELIVER_APP_PREFIX,
    SIM_DOMAIN,
    SIM_LEARN_APP_PREFIX,
)
from commcare_connect.simulation.steps import review as review_steps
from commcare_connect.simulation.steps.browser import StepRequestError
from commcare_connect.users.tests.factories import ConnectIdUserLinkFactory, MembershipFactory, MobileUserFactory

MAX_DAILY = 2


@pytest.fixture
def opportunity(user, organization, program_manager_org):
    """A managed opportunity reviewed by hand, with every visit flag on. ``user`` administers both sides."""
    MembershipFactory(user=user, organization=organization)
    MembershipFactory(user=user, organization=program_manager_org)
    api_key = HQApiKeyFactory()
    today = datetime.date.today()
    opportunity = OpportunityFactory(
        organization=organization,
        program=ProgramFactory(organization=program_manager_org),
        managed=True,
        hq_server=api_key.hq_server,
        api_key=api_key,
        learn_app=_app(api_key.hq_server, SIM_LEARN_APP_PREFIX),
        deliver_app=_app(api_key.hq_server, SIM_DELIVER_APP_PREFIX),
        start_date=today - datetime.timedelta(days=30),
        end_date=today + datetime.timedelta(days=30),
        total_budget=100_000,
        auto_approve_visits=False,
    )
    OpportunityVerificationFlagsFactory(opportunity=opportunity, duplicate=True, gps=True, location=500)
    payment_unit = PaymentUnitFactory(opportunity=opportunity, max_daily=MAX_DAILY, max_total=50, amount=5)
    deliver_unit = DeliverUnitFactory(
        app=opportunity.deliver_app, slug=DELIVER_UNIT_SLUGS[0], payment_unit=payment_unit
    )
    DeliverUnitFlagRulesFactory(opportunity=opportunity, deliver_unit=deliver_unit, duration=5)
    return opportunity


@pytest.fixture
def workers(opportunity):
    """Workers who have claimed the opportunity; claiming is the workers area's step."""
    workers = []
    for _ in range(2):
        worker = MobileUserFactory()
        ConnectIdUserLinkFactory(
            user=worker,
            commcare_username=f"{worker.username}@{SIM_DOMAIN}.commcarehq.org",
            hq_server=opportunity.hq_server,
        )
        access = OpportunityAccessFactory(user=worker, opportunity=opportunity, accepted=True)
        claim = OpportunityClaimFactory(opportunity_access=access, end_date=opportunity.end_date)
        OpportunityClaimLimit.create_claim_limits(opportunity, claim)
        workers.append(worker)
    return workers


@pytest.mark.django_db
def test_deliver_creates_visits_through_the_form_receiver(sim, opportunity, workers, httpx_mock):
    visits = sim.deliver(opportunity, workers, per_worker=5, over_days=7, duplicates=1, no_gps=1, too_short=1)

    assert len(visits) == 10
    for worker in workers:
        worker_visits = [visit for visit in visits if visit.user_id == worker.id]
        flags = Counter(flag for visit in worker_visits for flag, _ in (visit.flag_reason or {}).get("flags", []))
        assert flags == {"duplicate": 1, "gps": 1, "duration": 1}
        assert Counter(visit.visit_date.date() for visit in worker_visits).most_common(1)[0][1] <= MAX_DAILY
        # The duplicate repeats an earlier beneficiary, so it adds to that beneficiary's work.
        assert CompletedWork.objects.filter(opportunity_access__user=worker).count() == 4
    assert {visit.status for visit in visits} == {VisitValidationStatus.pending, VisitValidationStatus.duplicate}
    week_ago = now() - datetime.timedelta(days=7)
    assert all(week_ago < visit.visit_date < now() for visit in visits)


@pytest.mark.django_db
def test_deliver_records_each_visit_at_its_own_moment(sim, opportunity, workers):
    visits = sim.deliver(opportunity, workers[:1], per_worker=4, over_days=10)

    assert [visit.date_created.date() for visit in visits] == [visit.visit_date.date() for visit in visits]
    assert len({visit.visit_date.date() for visit in visits}) >= 2


@pytest.mark.django_db
def test_long_forms_still_finish_and_reach_connect_before_now(sim, opportunity, workers):
    DeliverUnitFlagRules.objects.filter(opportunity=opportunity).update(duration=10 * 60)

    visits = sim.deliver(opportunity, workers[:1], per_worker=4, over_days=10)

    assert not any(visit.flagged for visit in visits)
    for visit in visits:
        assert visit.date_created < now()
        assert visit.date_created.date() == visit.visit_date.date()
    access = OpportunityAccess.objects.get(opportunity=opportunity, user=workers[0])
    assert access.last_active < now()


@pytest.mark.django_db
def test_deliver_stays_inside_the_payment_unit_start(sim, opportunity, workers):
    start = datetime.date.today() - datetime.timedelta(days=3)
    opportunity.paymentunit_set.update(start_date=start)

    visits = sim.deliver(opportunity, workers[:1], per_worker=MAX_DAILY * 2, over_days=10)

    assert all(visit.visit_date.date() >= start for visit in visits)
    assert VisitValidationStatus.trial not in {visit.status for visit in visits}


@pytest.mark.django_db
@pytest.mark.parametrize(
    "limit, error",
    [
        ({"per_worker": MAX_DAILY * 3, "over_days": 2}, "daily limit"),
        ({"max_visits": 2, "per_worker": 3, "over_days": 7}, "left on their claim"),
    ],
)
def test_deliver_refuses_what_the_limits_would_mark_over_limit(sim, opportunity, workers, limit, error):
    if "max_visits" in limit:
        OpportunityClaimLimit.objects.filter(opportunity_claim__opportunity_access__user=workers[0]).update(
            max_visits=limit["max_visits"]
        )

    with pytest.raises(ValueError, match=error):
        sim.deliver(opportunity, workers[:1], per_worker=limit["per_worker"], over_days=limit["over_days"])


@pytest.mark.django_db
def test_deliver_needs_a_clean_form_per_worker(sim, opportunity, workers):
    with pytest.raises(ValueError, match="at least one clean form"):
        sim.deliver(opportunity, workers, per_worker=2, no_gps=1, too_short=1)


@pytest.mark.django_db
def test_work_becomes_payable_only_after_pm_review(sim, opportunity, workers):
    sim.deliver(opportunity, workers, per_worker=3, duplicates=1)

    sim.review(opportunity, approve=1.0)

    visits = UserVisit.objects.filter(opportunity=opportunity)
    assert {visit.status for visit in visits} == {VisitValidationStatus.approved}
    assert all(visit.justification for visit in visits if visit.flagged)
    assert _accrued(opportunity) == 0

    sim.pm_review(opportunity, agree=1.0)

    assert {visit.review_status for visit in visits.all()} == {VisitReviewStatus.agree}
    assert _accrued(opportunity) > 0


@pytest.mark.django_db
@pytest.mark.parametrize(
    "worker_count, per_worker, approve, reject, expected",
    [
        (2, 4, 0.5, 0.25, {VisitValidationStatus.approved: 4, VisitValidationStatus.rejected: 2, "pending": 2}),
        # Shares that add up to 1 leave nothing pending, however the counts round.
        (1, 5, 0.5, 0.5, {VisitValidationStatus.approved: 2, VisitValidationStatus.rejected: 3}),
    ],
)
def test_review_splits_visits_by_share(
    sim, opportunity, workers, worker_count, per_worker, approve, reject, expected, monkeypatch
):
    sim.deliver(opportunity, workers[:worker_count], per_worker=per_worker)
    # Several requests per decision, as when there is more than one page of visits.
    monkeypatch.setattr(review_steps, "REVIEW_BATCH_SIZE", 2)

    sim.review(opportunity, approve=approve, reject=reject)
    sim.pm_review(opportunity, agree=0.5)

    visits = UserVisit.objects.filter(opportunity=opportunity)
    assert Counter(visits.values_list("status", flat=True)) == expected
    assert all(visit.reason for visit in visits.filter(status=VisitValidationStatus.rejected))
    reviews = Counter(visits.filter(status=VisitValidationStatus.approved).values_list("review_status", flat=True))
    half = expected[VisitValidationStatus.approved] // 2
    assert reviews == {VisitReviewStatus.agree: half, VisitReviewStatus.disagree: half}


@pytest.mark.django_db
def test_reviews_are_stamped_with_the_simulated_time(sim, opportunity, workers):
    sim.deliver(opportunity, workers[:1], per_worker=2, over_days=10)
    moment = now() - datetime.timedelta(days=1)

    with sim.at(moment):
        sim.review(opportunity, approve=1.0)
        sim.pm_review(opportunity, agree=1.0)

    for visit in UserVisit.objects.filter(opportunity=opportunity):
        assert visit.review_created_on.date() == moment.date()
        assert visit.review_status_modified_date.date() == moment.date()


@pytest.mark.django_db
@pytest.mark.parametrize("side, role", [("nm", "viewer"), ("pm", "member")])
def test_review_refuses_users_the_review_pages_refuse(sim, opportunity, organization, program_manager_org, side, role):
    actor = MembershipFactory(organization=organization if side == "nm" else program_manager_org, role=role).user

    with pytest.raises(PermissionDenied):
        if side == "nm":
            sim.nm_admin = actor
            sim.review(opportunity, approve=1.0)
        else:
            sim.pm_admin = actor
            sim.pm_review(opportunity)


@pytest.mark.django_db
def test_review_raises_when_the_page_refuses_the_approval(sim, opportunity, workers, monkeypatch):
    sim.deliver(opportunity, workers, per_worker=3, duplicates=1)
    # Flagged visits need a justification; approve_visits refuses the request without one.
    monkeypatch.setattr(review_steps, "JUSTIFICATION", "")

    with pytest.raises(StepRequestError, match="Justification is mandatory"):
        sim.review(opportunity, approve=1.0)

    statuses = set(UserVisit.objects.filter(opportunity=opportunity).values_list("status", flat=True))
    assert statuses == {VisitValidationStatus.pending, VisitValidationStatus.duplicate}


@pytest.mark.django_db
def test_pm_review_raises_when_the_page_ignores_the_review(
    sim, opportunity, workers, program_manager_org, monkeypatch
):
    sim.deliver(opportunity, workers, per_worker=2)
    sim.review(opportunity, approve=1.0)
    # Past the step's own check, a PM-organization member reaches user_visit_review, which ignores them.
    sim.pm_admin = MembershipFactory(organization=program_manager_org, role="member").user
    monkeypatch.setattr(review_steps, "is_opportunity_pm", lambda request, opportunity: True)

    with pytest.raises(StepRequestError):
        sim.pm_review(opportunity, agree=1.0)

    reviews = set(UserVisit.objects.filter(opportunity=opportunity).values_list("review_status", flat=True))
    assert reviews == {VisitReviewStatus.pending}


@pytest.mark.django_db
def test_review_refuses_automatically_verified_opportunities(sim, opportunity):
    opportunity.automatic_visit_verification = True
    opportunity.save()

    with pytest.raises(ValueError, match="automatically"):
        sim.review(opportunity, approve=1.0)


def _app(hq_server, prefix):
    return CommCareAppFactory(hq_server=hq_server, cc_domain=SIM_DOMAIN, cc_app_id=f"{prefix}{hq_server.pk}")


def _accrued(opportunity):
    return sum(OpportunityAccess.objects.filter(opportunity=opportunity).values_list("payment_accrued", flat=True))
