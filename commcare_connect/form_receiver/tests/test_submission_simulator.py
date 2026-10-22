from datetime import UTC, datetime
from uuid import UUID

import pytest
from django.http import Http404
from django.template import Context, Template
from django.utils.timezone import now

from commcare_connect.form_receiver.processor import (
    ASSESSMENT_JSONPATH,
    DELIVER_UNIT_JSONPATH,
    LEARN_MODULE_JSONPATH,
    _get_matching_blocks,
)
from commcare_connect.form_receiver.serializers import XFormSerializer
from commcare_connect.form_receiver.submission_simulator import (
    FORM_DURATION,
    accept_invite_for_worker,
    build_assessment_form,
    build_deliver_form,
    build_learn_form,
    claim_for_worker,
    get_commcare_username,
    get_worker_rows,
    start_learning_for_worker,
    submit_test_form,
)
from commcare_connect.form_receiver.submission_simulator_types import SubmissionOverrides, WorkerStep
from commcare_connect.opportunity.models import (
    OpportunityAccess,
    OpportunityClaim,
    OpportunityClaimLimit,
    PaymentUnit,
    UserInviteStatus,
    UserVisit,
    VisitValidationStatus,
)
from commcare_connect.opportunity.tests.factories import (
    DeliverUnitFactory,
    LearnModuleFactory,
    OpportunityAccessFactory,
    OpportunityFactory,
    PaymentUnitFactory,
    UserInviteFactory,
)
from commcare_connect.users.models import ConnectIDUserLink
from commcare_connect.users.tests.factories import ConnectIdUserLinkFactory
from commcare_connect.utils.local_testing import ensure_simulator_enabled, simulator_enabled


@pytest.mark.parametrize(
    "debug, enabled, expected",
    [(True, True, True), (True, False, False), (False, True, False), (False, False, False)],
)
def test_simulator_enabled(settings, debug, enabled, expected):
    settings.DEBUG = debug
    settings.ENABLE_TEST_SUBMISSIONS = enabled
    assert simulator_enabled() is expected


def test_ensure_simulator_enabled_raises_404_when_off(settings):
    settings.DEBUG = True
    settings.ENABLE_TEST_SUBMISSIONS = False
    with pytest.raises(Http404):
        ensure_simulator_enabled()


def test_setting_defaults_off_in_base_settings():
    from config.settings import base

    assert base.ENABLE_TEST_SUBMISSIONS is False


@pytest.mark.parametrize("enabled, expected", [(True, "yes"), (False, "no")])
def test_submission_simulator_enabled_template_tag(settings, enabled, expected):
    settings.DEBUG = True
    settings.ENABLE_TEST_SUBMISSIONS = enabled
    template = Template(
        "{% load submission_simulator %}{% submission_simulator_enabled as on %}{% if on %}yes{% else %}no{% endif %}"
    )
    assert template.render(Context()) == expected


@pytest.fixture
def deliver_unit(opportunity):
    return DeliverUnitFactory(app=opportunity.deliver_app, payment_unit__opportunity=opportunity)


def _domain_username(app, name="test"):
    return f"{name}@{app.cc_domain}.commcarehq.org"


def _parse(payload):
    serializer = XFormSerializer(data=payload)
    assert serializer.is_valid(), serializer.errors
    return serializer.save()


def test_get_commcare_username_matches_link_without_server(opportunity, mobile_user):
    ConnectIdUserLinkFactory(
        user=mobile_user, commcare_username=_domain_username(opportunity.deliver_app), hq_server=None
    )
    assert get_commcare_username(mobile_user, opportunity.deliver_app) == _domain_username(opportunity.deliver_app)


def test_get_commcare_username_per_app_domain(opportunity, user_with_connectid_link):
    for app in (opportunity.learn_app, opportunity.deliver_app):
        assert get_commcare_username(user_with_connectid_link, app) == _domain_username(app)


def test_get_commcare_username_is_case_insensitive_on_domain(opportunity, mobile_user):
    username = f"test@{opportunity.deliver_app.cc_domain.upper()}.commcarehq.org"
    ConnectIdUserLinkFactory(user=mobile_user, commcare_username=username)
    assert get_commcare_username(mobile_user, opportunity.deliver_app) == username


def test_get_commcare_username_prefers_link_on_app_server(opportunity, mobile_user):
    app = opportunity.deliver_app
    ConnectIdUserLinkFactory(user=mobile_user, commcare_username=_domain_username(app, "other"))
    ConnectIdUserLinkFactory(user=mobile_user, commcare_username=_domain_username(app, "own"), hq_server=app.hq_server)
    ConnectIdUserLinkFactory(user=mobile_user, commcare_username=_domain_username(app, "zzz"))
    assert get_commcare_username(mobile_user, app) == _domain_username(app, "own")


def test_get_commcare_username_ignores_other_domains_and_users(opportunity, mobile_user):
    ConnectIdUserLinkFactory(user=mobile_user, commcare_username="test@some-other-domain.commcarehq.org")
    ConnectIdUserLinkFactory(commcare_username=_domain_username(opportunity.deliver_app))
    assert get_commcare_username(mobile_user, opportunity.deliver_app) is None


def _build(kind, opportunity, deliver_unit=None, overrides=SubmissionOverrides()):
    if kind == "deliver":
        return opportunity.deliver_app, build_deliver_form(
            opportunity.deliver_app, "u@d.commcarehq.org", deliver_unit, overrides
        )
    if kind == "learn":
        modules = [LearnModuleFactory(app=opportunity.learn_app)]
        return opportunity.learn_app, build_learn_form(opportunity.learn_app, "u@d.commcarehq.org", modules, overrides)
    return opportunity.learn_app, build_assessment_form(opportunity.learn_app, "u@d.commcarehq.org", 73, overrides)


@pytest.mark.parametrize("kind", ["deliver", "learn", "assessment"])
def test_built_payload_passes_serializer(opportunity, deliver_unit, kind):
    app, payload = _build(kind, opportunity, deliver_unit)
    xform = _parse(payload)
    assert xform.domain == app.cc_domain
    assert xform.app_id == app.cc_app_id
    assert xform.metadata.username == "u@d.commcarehq.org"
    assert "attachments" not in payload


def test_each_build_has_fresh_xform_id(opportunity, deliver_unit):
    _, first = _build("deliver", opportunity, deliver_unit)
    _, second = _build("deliver", opportunity, deliver_unit)
    assert first["id"] != second["id"]
    assert first["metadata"]["instanceID"] == first["id"]


def test_deliver_blocks_found_by_processor(opportunity, deliver_unit):
    _, payload = _build("deliver", opportunity, deliver_unit, SubmissionOverrides(deliver_block_count=3))
    blocks = _get_matching_blocks(DELIVER_UNIT_JSONPATH, _parse(payload))
    assert len(blocks) == 3
    assert {block["@id"] for block in blocks} == {deliver_unit.slug}
    assert len({block["entity_id"] for block in blocks}) == 3
    UUID(blocks[0]["entity_id"])


def test_deliver_overrides_applied(opportunity, deliver_unit):
    visit_time = datetime(2026, 1, 2, 10, 0, tzinfo=UTC)
    overrides = SubmissionOverrides(
        entity_id="e-1", entity_name="Entity one", visit_time=visit_time, latitude=1.5, longitude=2.5
    )
    _, payload = _build("deliver", opportunity, deliver_unit, overrides)
    xform = _parse(payload)
    [block] = _get_matching_blocks(DELIVER_UNIT_JSONPATH, xform)
    assert block["entity_id"] == "e-1"
    assert block["entity_name"] == "Entity one"
    assert xform.metadata.timeStart == visit_time
    assert xform.metadata.timeEnd == visit_time + FORM_DURATION
    assert xform.metadata.location == "1.5 2.5 0 10"


@pytest.mark.parametrize("latitude, longitude", [(1.5, None), (None, 2.5), (None, None)])
def test_location_blank_without_both_coordinates(opportunity, deliver_unit, latitude, longitude):
    _, payload = _build(
        "deliver", opportunity, deliver_unit, SubmissionOverrides(latitude=latitude, longitude=longitude)
    )
    assert _parse(payload).metadata.location is None


def test_learn_form_has_one_block_per_module(opportunity):
    modules = LearnModuleFactory.create_batch(2, app=opportunity.learn_app)
    payload = build_learn_form(opportunity.learn_app, "u@d.commcarehq.org", modules, SubmissionOverrides())
    blocks = _get_matching_blocks(LEARN_MODULE_JSONPATH, _parse(payload))
    assert sorted(block["@id"] for block in blocks) == sorted(module.slug for module in modules)


def test_assessment_block_carries_score(opportunity):
    _, payload = _build("assessment", opportunity)
    [block] = _get_matching_blocks(ASSESSMENT_JSONPATH, _parse(payload))
    assert block["user_score"] == "73"


@pytest.fixture
def worker_access(opportunity, mobile_user_with_connect_link):
    return OpportunityAccess.objects.get(user=mobile_user_with_connect_link, opportunity=opportunity)


@pytest.fixture
def claimed_deliver_unit(opportunity, worker_access):
    # A payment unit the worker has a claim limit for. The factory's limits are random (max_daily can
    # be 1), so pin them high enough that several same-day visits never go over limit.
    payment_unit = PaymentUnit.objects.filter(opportunity=opportunity).first()
    payment_unit.max_daily, payment_unit.max_total = 10, 20
    payment_unit.save()
    OpportunityClaimLimit.objects.filter(
        opportunity_claim__opportunity_access=worker_access, payment_unit=payment_unit
    ).update(max_visits=20)
    return DeliverUnitFactory(app=opportunity.deliver_app, payment_unit=payment_unit)


def _submit_deliver(opportunity, access, deliver_unit, overrides=SubmissionOverrides()):
    app = opportunity.deliver_app
    payload = build_deliver_form(app, get_commcare_username(access.user, app), deliver_unit, overrides)
    return submit_test_form(payload, app, access)


def _submit_learn(opportunity, access, modules):
    app = opportunity.learn_app
    payload = build_learn_form(app, get_commcare_username(access.user, app), modules, SubmissionOverrides())
    return submit_test_form(payload, app, access)


def test_deliver_submission_creates_visit_for_worker_and_unit(opportunity, worker_access, claimed_deliver_unit):
    outcome = _submit_deliver(opportunity, worker_access, claimed_deliver_unit)
    assert outcome.error is None
    [visit] = outcome.visits
    assert visit.user == worker_access.user
    assert visit.deliver_unit == claimed_deliver_unit
    assert visit.xform_id == outcome.payload["id"]
    assert not outcome.recorded_nothing


@pytest.mark.parametrize("opportunity", [{"verification_flags": {"duplicate": True}}], indirect=True)
def test_reused_entity_is_duplicate(opportunity, worker_access, claimed_deliver_unit):
    overrides = SubmissionOverrides(entity_id="e-1", entity_name="Entity one")
    _submit_deliver(opportunity, worker_access, claimed_deliver_unit, overrides)
    outcome = _submit_deliver(opportunity, worker_access, claimed_deliver_unit, overrides)
    [visit] = outcome.visits
    assert visit.status == VisitValidationStatus.duplicate


def test_missing_claim_returns_processing_error(opportunity, mobile_user):
    ConnectIdUserLinkFactory(user=mobile_user, commcare_username=_domain_username(opportunity.deliver_app))
    access = OpportunityAccess.objects.get(user=mobile_user, opportunity=opportunity)
    deliver_unit = DeliverUnitFactory(
        app=opportunity.deliver_app, payment_unit=PaymentUnit.objects.filter(opportunity=opportunity).first()
    )
    outcome = _submit_deliver(opportunity, access, deliver_unit)
    assert "has not claimed" in outcome.error
    assert outcome.visits == []


def test_unlinked_deliver_unit_returns_processing_error(opportunity, worker_access):
    deliver_unit = DeliverUnitFactory(app=opportunity.deliver_app, payment_unit=None)
    outcome = _submit_deliver(opportunity, worker_access, deliver_unit)
    assert "Payment unit is not configured" in outcome.error


def test_inactive_opportunity_records_nothing(opportunity, worker_access, claimed_deliver_unit):
    opportunity.active = False
    opportunity.save()
    outcome = _submit_deliver(opportunity, worker_access, claimed_deliver_unit)
    assert outcome.error is None
    assert outcome.recorded_nothing


def test_invalid_payload_returns_error_without_processing(opportunity, worker_access, claimed_deliver_unit):
    payload = build_deliver_form(
        opportunity.deliver_app, "u@d.commcarehq.org", claimed_deliver_unit, SubmissionOverrides()
    )
    del payload["metadata"]["timeStart"]
    outcome = submit_test_form(payload, opportunity.deliver_app, worker_access)
    assert "timeStart" in outcome.error
    assert outcome.visits == []


def test_error_rolls_back_only_this_form(opportunity, worker_access, claimed_deliver_unit):
    first = _submit_deliver(opportunity, worker_access, claimed_deliver_unit)
    unlinked = DeliverUnitFactory(app=opportunity.deliver_app, payment_unit=None)
    assert _submit_deliver(opportunity, worker_access, unlinked).error
    assert UserVisit.objects.filter(xform_id=first.payload["id"]).exists()


def test_learn_submission_completes_modules_and_learning(opportunity, worker_access):
    modules = LearnModuleFactory.create_batch(2, app=opportunity.learn_app)
    outcome = _submit_learn(opportunity, worker_access, modules)
    assert outcome.error is None
    assert sorted(cm.module_id for cm in outcome.completed_modules) == sorted(m.pk for m in modules)
    assert outcome.completed_learn_date is not None


def test_partial_learning_leaves_completed_learn_date_empty(opportunity, worker_access):
    modules = LearnModuleFactory.create_batch(2, app=opportunity.learn_app)
    outcome = _submit_learn(opportunity, worker_access, modules[:1])
    assert len(outcome.completed_modules) == 1
    assert outcome.completed_learn_date is None


@pytest.mark.parametrize("score, passed", [(80, True), (60, False)])
def test_assessment_submission_records_score(opportunity, worker_access, score, passed):
    app = opportunity.learn_app
    app.passing_score = 70
    app.save()
    payload = build_assessment_form(app, get_commcare_username(worker_access.user, app), score, SubmissionOverrides())
    outcome = submit_test_form(payload, app, worker_access)
    [assessment] = outcome.assessments
    assert (assessment.score, assessment.passing_score, assessment.passed) == (score, 70, passed)


def test_get_worker_rows_lists_every_worker_with_usernames(opportunity, worker_access, mobile_user):
    rows = {row.access.pk: row for row in get_worker_rows(opportunity)}
    assert set(rows) == {worker_access.pk, OpportunityAccess.objects.get(user=mobile_user).pk}
    row = rows[worker_access.pk]
    assert row.deliver_username == _domain_username(opportunity.deliver_app)
    assert row.learn_username == _domain_username(opportunity.learn_app)
    assert rows[OpportunityAccess.objects.get(user=mobile_user).pk].deliver_username is None


def test_get_worker_rows_recent_entities_are_distinct_newest_first(opportunity, worker_access, claimed_deliver_unit):
    for entity_id, day in [("e-1", 1), ("e-2", 2), ("e-1", 3)]:
        visit_time = datetime(2026, 1, day, 10, 0, tzinfo=UTC)
        _submit_deliver(
            opportunity,
            worker_access,
            claimed_deliver_unit,
            SubmissionOverrides(entity_id=entity_id, entity_name=f"Name {entity_id}", visit_time=visit_time),
        )
    assert _row_for(opportunity, worker_access).recent_entities == [("e-1", "Name e-1"), ("e-2", "Name e-2")]


def test_multiple_deliver_blocks_record_one_visit_each(opportunity, worker_access, claimed_deliver_unit):
    overrides = SubmissionOverrides(entity_id="e-1", deliver_block_count=3)
    outcome = _submit_deliver(opportunity, worker_access, claimed_deliver_unit, overrides)
    assert outcome.error is None
    assert sorted(visit.entity_id for visit in outcome.visits) == ["e-1", "e-1-2", "e-1-3"]


@pytest.mark.parametrize("missing_app", ["learn_app", "deliver_app"])
def test_get_worker_rows_without_app(opportunity, worker_access, missing_app):
    setattr(opportunity, missing_app, None)
    opportunity.save()
    username_field = "learn_username" if missing_app == "learn_app" else "deliver_username"
    assert getattr(_row_for(opportunity, worker_access), username_field) is None


def _unclaimed_access(opportunity, mobile_user):
    return OpportunityAccess.objects.get(user=mobile_user, opportunity=opportunity)


def _row_for(opportunity, access):
    [row] = [row for row in get_worker_rows(opportunity) if row.access == access]
    return row


def test_claim_unclaimed_worker_creates_claim_and_limits(opportunity, mobile_user):
    access = _unclaimed_access(opportunity, mobile_user)
    outcome = claim_for_worker(access)
    assert outcome.error is None
    assert outcome.claim_created
    claim = OpportunityClaim.objects.get(opportunity_access=access)
    assert claim.end_date == opportunity.end_date
    assert {limit.payment_unit for limit in outcome.created_limits} == set(opportunity.paymentunit_set.all())
    assert outcome.skipped_payment_units == []


def test_claim_claimed_worker_adds_missing_limits(opportunity, worker_access):
    new_unit = PaymentUnitFactory(opportunity=opportunity, parent_payment_unit=None)
    opportunity.total_budget += new_unit.max_total * (new_unit.amount + new_unit.org_amount)
    opportunity.save()
    outcome = claim_for_worker(worker_access)
    assert outcome.error is None
    assert not outcome.claim_created
    assert [limit.payment_unit for limit in outcome.created_limits] == [new_unit]


def test_claim_reports_payment_units_the_budget_cannot_fund(opportunity, worker_access):
    # the worker fixture sizes total_budget to exactly its existing units
    new_unit = PaymentUnitFactory(opportunity=opportunity, parent_payment_unit=None)
    outcome = claim_for_worker(worker_access)
    assert outcome.created_limits == []
    assert outcome.skipped_payment_units == [new_unit]


@pytest.mark.parametrize(
    "field, value, expected_error",
    [("total_budget", 0, "full"), ("end_date", datetime(2020, 1, 1).date(), "ended")],
)
def test_claim_refused(opportunity, mobile_user, field, value, expected_error):
    setattr(opportunity, field, value)
    opportunity.save()
    access = _unclaimed_access(opportunity, mobile_user)
    assert expected_error in claim_for_worker(access).error
    assert not OpportunityClaim.objects.filter(opportunity_access=access).exists()


@pytest.mark.parametrize(
    "accepted, learning_started, expected",
    [(False, False, WorkerStep.ACCEPT), (True, False, WorkerStep.LEARN), (True, True, WorkerStep.CLAIM)],
)
def test_next_step_for_unclaimed_worker(opportunity, mobile_user, accepted, learning_started, expected):
    access = _unclaimed_access(opportunity, mobile_user)
    access.accepted = accepted
    access.date_learn_started = now() if learning_started else None
    access.save()
    assert _row_for(opportunity, access).next_step == expected


def _link_usernames(user):
    return set(ConnectIDUserLink.objects.filter(user=user).values_list("commcare_username", "domain", "hq_server_id"))


def test_start_learning_links_learn_domain_and_starts_learning(opportunity, mobile_user):
    access = _unclaimed_access(opportunity, mobile_user)
    UserInviteFactory(opportunity=opportunity, opportunity_access=access)
    assert start_learning_for_worker(access) is None
    start_learning_for_worker(access)  # a second start doesn't add another link
    access.refresh_from_db()
    assert access.accepted
    assert access.date_learn_started is not None
    assert access.last_active == access.date_learn_started
    domain = opportunity.learn_app.cc_domain
    assert _link_usernames(mobile_user) == {
        (f"{mobile_user.username.lower()}@{domain}.commcarehq.org", domain, opportunity.hq_server_id)
    }
    assert get_commcare_username(mobile_user, opportunity.learn_app) is not None


def test_claim_links_deliver_domain(opportunity, mobile_user):
    access = _unclaimed_access(opportunity, mobile_user)
    claim_for_worker(access)
    assert get_commcare_username(mobile_user, opportunity.deliver_app) == (
        f"{mobile_user.username.lower()}@{opportunity.deliver_app.cc_domain}.commcarehq.org"
    )


@pytest.mark.parametrize(
    "new_payment_unit, fund_it, expected",
    [(False, False, None), (True, True, WorkerStep.LIMITS), (True, False, None)],
)
def test_next_step_for_claimed_worker(opportunity, worker_access, new_payment_unit, fund_it, expected):
    # The worker fixture sizes total_budget to exactly its existing units, so a new unit is only
    # fundable once the budget grows. An unfundable one must not leave "Create claim limits" stuck.
    if new_payment_unit:
        new_unit = PaymentUnitFactory(opportunity=opportunity, parent_payment_unit=None)
        if fund_it:
            opportunity.total_budget += new_unit.max_total * (new_unit.amount + new_unit.org_amount)
            opportunity.save()
    assert _row_for(opportunity, worker_access).next_step == expected


def test_accept_invite_marks_access_and_invite_accepted(opportunity, mobile_user):
    access = _unclaimed_access(opportunity, mobile_user)
    invite = UserInviteFactory(opportunity=opportunity, opportunity_access=access)
    assert accept_invite_for_worker(access) is None
    access.refresh_from_db()
    invite.refresh_from_db()
    assert access.accepted
    assert invite.status == UserInviteStatus.accepted


@pytest.mark.parametrize("step", [accept_invite_for_worker, start_learning_for_worker])
def test_steps_without_user_invite_change_nothing(opportunity, mobile_user, step):
    access = _unclaimed_access(opportunity, mobile_user)
    assert "no invite" in step(access).lower()
    access.refresh_from_db()
    assert not access.accepted
    assert not ConnectIDUserLink.objects.filter(user=mobile_user).exists()


def test_claim_refused_when_payment_unit_has_no_max_total(opportunity, mobile_user):
    PaymentUnitFactory(opportunity=opportunity, parent_payment_unit=None, name="Unbounded", max_total=None)
    access = _unclaimed_access(opportunity, mobile_user)
    assert "Unbounded" in claim_for_worker(access).error
    assert not OpportunityClaim.objects.filter(opportunity_access=access).exists()


def test_submission_recorded_on_another_opportunity_is_reported(opportunity, worker_access):
    # Another active opportunity shares the learn app; this one is inactive, so the processor
    # records the form against the other one
    other = OpportunityFactory(learn_app=opportunity.learn_app, active=True)
    OpportunityAccessFactory(user=worker_access.user, opportunity=other)
    opportunity.active = False
    opportunity.save()
    module = LearnModuleFactory(app=opportunity.learn_app)
    outcome = _submit_learn(opportunity, worker_access, [module])
    assert outcome.other_opportunities == [other]
    assert not outcome.recorded_nothing


def test_submission_on_this_opportunity_reports_no_other(opportunity, worker_access):
    module = LearnModuleFactory(app=opportunity.learn_app)
    assert _submit_learn(opportunity, worker_access, [module]).other_opportunities == []
