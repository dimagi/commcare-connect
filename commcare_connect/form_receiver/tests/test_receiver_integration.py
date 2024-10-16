import datetime
from copy import deepcopy
from uuid import uuid4

import pytest
from rest_framework.test import APIClient

from commcare_connect.form_receiver.tests.test_receiver_endpoint import add_credentials
from commcare_connect.form_receiver.tests.xforms import (
    AssessmentStubFactory,
    DeliverUnitStubFactory,
    LearnModuleJsonFactory,
    get_form_json,
)
from commcare_connect.opportunity.models import (
    Assessment,
    CompletedModule,
    CompletedWork,
    CompletedWorkStatus,
    LearnModule,
    Opportunity,
    OpportunityAccess,
    OpportunityClaimLimit,
    OpportunityVerificationFlags,
    UserVisit,
    VisitReviewStatus,
    VisitValidationStatus,
)
from commcare_connect.opportunity.tasks import bulk_approve_completed_work
from commcare_connect.opportunity.tests.factories import (
    CatchmentAreaFactory,
    DeliverUnitFactory,
    DeliverUnitFlagRulesFactory,
    FormJsonValidationRulesFactory,
    LearnModuleFactory,
    OpportunityAccessFactory,
    OpportunityClaimFactory,
    PaymentUnitFactory,
)
from commcare_connect.opportunity.visit_import import update_payment_accrued
from commcare_connect.users.models import User


@pytest.mark.django_db
def test_form_receiver_learn_module(
    mobile_user_with_connect_link: User, api_client: APIClient, opportunity: Opportunity
):
    module_id = "learn_module_1"
    form_json = _get_form_json(opportunity.learn_app, module_id)
    assert CompletedModule.objects.count() == 0
    learn_module = LearnModuleFactory(app=opportunity.learn_app, slug=module_id)
    make_request(api_client, form_json, mobile_user_with_connect_link)

    assert CompletedModule.objects.count() == 1
    assert CompletedModule.objects.filter(
        module=learn_module,
        xform_id=form_json["id"],
        app_build_id=form_json["build_id"],
        app_build_version=form_json["metadata"]["app_build_version"],
    ).exists()


@pytest.mark.django_db
def test_form_receiver_learn_module_create(
    mobile_user_with_connect_link: User, api_client: APIClient, opportunity: Opportunity
):
    """Test that a new learn module is created if it doesn't exist."""
    module = LearnModuleJsonFactory()
    form_json = _get_form_json(opportunity.learn_app, module.id, module.json)
    assert CompletedModule.objects.count() == 0

    make_request(api_client, form_json, mobile_user_with_connect_link)
    assert CompletedModule.objects.count() == 1
    assert CompletedModule.objects.filter(
        module__slug=module.id,
        xform_id=form_json["id"],
        app_build_id=form_json["build_id"],
        app_build_version=form_json["metadata"]["app_build_version"],
    ).exists()

    assert LearnModule.objects.filter(
        app=opportunity.learn_app,
        slug=module.id,
        name=module.name,
        description=module.description,
        time_estimate=module.time_estimate,
    ).exists()


@pytest.mark.django_db
def test_form_receiver_assessment(
    mobile_user_with_connect_link: User, api_client: APIClient, opportunity: Opportunity
):
    passing_score = opportunity.learn_app.passing_score
    score = passing_score + 5
    assessment = AssessmentStubFactory(score=score).json
    form_json = get_form_json(
        form_block=assessment,
        domain=opportunity.learn_app.cc_domain,
        app_id=opportunity.learn_app.cc_app_id,
    )
    assert Assessment.objects.count() == 0

    make_request(api_client, form_json, mobile_user_with_connect_link)
    assert Assessment.objects.count() == 1
    assert Assessment.objects.filter(
        score=score,
        passing_score=passing_score,
        passed=True,
        xform_id=form_json["id"],
        app_build_id=form_json["build_id"],
        app_build_version=form_json["metadata"]["app_build_version"],
    ).exists()


@pytest.mark.django_db
def test_receiver_deliver_form(mobile_user_with_connect_link: User, api_client: APIClient, opportunity: Opportunity):
    deliver_unit = DeliverUnitFactory(app=opportunity.deliver_app, payment_unit=opportunity.paymentunit_set.first())
    stub = DeliverUnitStubFactory(id=deliver_unit.slug)
    form_json = get_form_json(
        form_block=stub.json,
        domain=deliver_unit.app.cc_domain,
        app_id=deliver_unit.app.cc_app_id,
    )
    assert UserVisit.objects.filter(user=mobile_user_with_connect_link).count() == 0

    make_request(api_client, form_json, mobile_user_with_connect_link)
    assert UserVisit.objects.filter(user=mobile_user_with_connect_link).count() == 1
    visit = UserVisit.objects.get(user=mobile_user_with_connect_link)
    assert visit.deliver_unit == deliver_unit
    assert visit.entity_id == stub.entity_id
    assert visit.entity_name == stub.entity_name


def _create_opp_and_form_json(
    opportunity,
    user,
    max_visits_per_user=100,
    daily_max_per_user=10,
    end_date=datetime.date.today(),
):
    payment_unit = PaymentUnitFactory(
        opportunity=opportunity, max_daily=daily_max_per_user, max_total=max_visits_per_user
    )
    access = OpportunityAccessFactory(user=user, opportunity=opportunity, accepted=True)
    claim = OpportunityClaimFactory(end_date=end_date, opportunity_access=access)
    OpportunityClaimLimit.create_claim_limits(opportunity, claim)

    deliver_unit = DeliverUnitFactory(app=opportunity.deliver_app, payment_unit=payment_unit)
    stub = DeliverUnitStubFactory(id=deliver_unit.slug)
    form_json = get_form_json(
        form_block=stub.json,
        domain=deliver_unit.app.cc_domain,
        app_id=deliver_unit.app.cc_app_id,
    )
    return form_json


@pytest.mark.django_db
def test_receiver_deliver_form_daily_visits_reached(
    user_with_connectid_link: User, api_client: APIClient, opportunity: Opportunity
):
    form_json = _create_opp_and_form_json(opportunity, user=user_with_connectid_link, daily_max_per_user=0)
    assert UserVisit.objects.filter(user=user_with_connectid_link).count() == 0
    make_request(api_client, form_json, user_with_connectid_link)
    assert UserVisit.objects.filter(user=user_with_connectid_link).count() == 1
    visit = UserVisit.objects.get(user=user_with_connectid_link)
    assert visit.status == VisitValidationStatus.over_limit


@pytest.mark.django_db
@pytest.mark.parametrize("paymentunit_options", [pytest.param({"max_daily": 2})])
def test_receiver_deliver_form_max_visits_reached(
    mobile_user_with_connect_link: User, api_client: APIClient, opportunity: Opportunity
):
    def submit_form_for_random_entity(form_json):
        duplicate_json = deepcopy(form_json)
        duplicate_json["form"]["deliver"]["entity_id"] = str(uuid4())
        make_request(api_client, duplicate_json, mobile_user_with_connect_link)

    payment_units = opportunity.paymentunit_set.all()
    form_json1 = get_form_json_for_payment_unit(payment_units[0])
    form_json2 = get_form_json_for_payment_unit(payment_units[1])
    for _ in range(2):
        submit_form_for_random_entity(form_json1)
        submit_form_for_random_entity(form_json2)
    assert UserVisit.objects.filter(user=mobile_user_with_connect_link).count() == 4
    # Limit reached
    submit_form_for_random_entity(form_json2)
    user_visits = UserVisit.objects.filter(user=mobile_user_with_connect_link)
    assert user_visits.count() == 5
    # First four are not over-limit
    assert {u.status for u in user_visits[0:4]} == {VisitValidationStatus.pending, VisitValidationStatus.approved}
    # Last one is over limit
    assert user_visits[4].status == VisitValidationStatus.over_limit


@pytest.mark.django_db
def test_receiver_deliver_form_end_date_reached(
    user_with_connectid_link: User, api_client: APIClient, opportunity: Opportunity
):
    form_json = _create_opp_and_form_json(
        opportunity, user=user_with_connectid_link, end_date=datetime.date.today() - datetime.timedelta(days=100)
    )
    assert UserVisit.objects.filter(user=user_with_connectid_link).count() == 0
    assert CompletedWork.objects.count() == 0
    make_request(api_client, form_json, user_with_connectid_link)
    assert UserVisit.objects.filter(user=user_with_connectid_link).count() == 1
    assert CompletedWork.objects.count() == 1
    visit = UserVisit.objects.get(user=user_with_connectid_link)
    assert visit.status == VisitValidationStatus.over_limit


@pytest.mark.django_db
def test_receiver_deliver_form_before_start_date(
    user_with_connectid_link: User, api_client: APIClient, opportunity: Opportunity
):
    opportunity.start_date = datetime.date.today() + datetime.timedelta(days=10)
    opportunity.save()
    form_json = _create_opp_and_form_json(
        opportunity, user=user_with_connectid_link, end_date=datetime.date.today() + datetime.timedelta(days=100)
    )
    assert UserVisit.objects.filter(user=user_with_connectid_link).count() == 0
    make_request(api_client, form_json, user_with_connectid_link)
    assert UserVisit.objects.filter(user=user_with_connectid_link).count() == 1
    visit = UserVisit.objects.get(user=user_with_connectid_link)
    assert visit.status == VisitValidationStatus.trial
    assert CompletedWork.objects.count() == 0


def test_receiver_duplicate(user_with_connectid_link: User, api_client: APIClient, opportunity: Opportunity):
    form_json = _create_opp_and_form_json(opportunity, user=user_with_connectid_link)
    make_request(api_client, form_json, user_with_connectid_link)
    visit = UserVisit.objects.get(user=user_with_connectid_link)
    assert visit.status == VisitValidationStatus.approved
    duplicate_json = deepcopy(form_json)
    duplicate_json["id"] = str(uuid4())
    make_request(api_client, duplicate_json, user_with_connectid_link)
    visit = UserVisit.objects.get(xform_id=duplicate_json["id"])
    assert visit.status == VisitValidationStatus.duplicate
    assert ["duplicate", "A beneficiary with the same identifier already exists"] in visit.flag_reason.get("flags", [])


def test_flagged_form(user_with_connectid_link: User, api_client: APIClient, opportunity: Opportunity):
    # The mock data for form fails with duration flag
    form_json = _create_opp_and_form_json(opportunity, user=user_with_connectid_link)
    deliver_unit = opportunity.deliver_app.deliver_units.first()
    DeliverUnitFlagRulesFactory(deliver_unit=deliver_unit, opportunity=opportunity, duration=1)
    make_request(api_client, form_json, user_with_connectid_link)
    visit = UserVisit.objects.get(user=user_with_connectid_link)
    assert visit.status == VisitValidationStatus.pending
    assert visit.flagged
    assert len(visit.flag_reason.get("flags", []))


def test_auto_approve_unflagged_visits(
    user_with_connectid_link: User, api_client: APIClient, opportunity: Opportunity
):
    form_json = _create_opp_and_form_json(opportunity, user=user_with_connectid_link)
    form_json["metadata"]["timeEnd"] = "2023-06-07T12:36:10.178000Z"
    opportunity.auto_approve_visits = True
    opportunity.save()
    make_request(api_client, form_json, user_with_connectid_link)
    visit = UserVisit.objects.get(user=user_with_connectid_link)
    assert not visit.flagged
    assert visit.status == VisitValidationStatus.approved


def test_auto_approve_flagged_visits(user_with_connectid_link: User, api_client: APIClient, opportunity: Opportunity):
    form_json = _create_opp_and_form_json(opportunity, user=user_with_connectid_link)
    opportunity.auto_approve_visits = True
    opportunity.save()
    deliver_unit = opportunity.deliver_app.deliver_units.first()
    DeliverUnitFlagRulesFactory(deliver_unit=deliver_unit, opportunity=opportunity, duration=1)
    make_request(api_client, form_json, user_with_connectid_link)
    visit = UserVisit.objects.get(user=user_with_connectid_link)
    assert visit.flagged
    assert visit.status == VisitValidationStatus.pending


def test_auto_approve_payments_flagged_visit(
    user_with_connectid_link: User, api_client: APIClient, opportunity: Opportunity
):
    # Flagged Visit
    form_json = _create_opp_and_form_json(opportunity, user=user_with_connectid_link)
    opportunity.auto_approve_payments = True
    opportunity.save()
    deliver_unit = opportunity.deliver_app.deliver_units.first()
    DeliverUnitFlagRulesFactory(deliver_unit=deliver_unit, opportunity=opportunity, duration=1)
    make_request(api_client, form_json, user_with_connectid_link)
    visit = UserVisit.objects.get(user=user_with_connectid_link)
    assert visit.flagged
    assert visit.status == VisitValidationStatus.pending

    # No Payment Approval
    update_payment_accrued(opportunity, users=[user_with_connectid_link])
    access = OpportunityAccess.objects.get(user=user_with_connectid_link, opportunity=opportunity)
    completed_work = CompletedWork.objects.get(opportunity_access=access)
    assert completed_work.status == CompletedWorkStatus.pending
    assert access.payment_accrued == 0


def test_auto_approve_payments_unflagged_visit(
    user_with_connectid_link: User, api_client: APIClient, opportunity: Opportunity
):
    form_json = _create_opp_and_form_json(opportunity, user=user_with_connectid_link)
    form_json["metadata"]["timeEnd"] = "2023-06-07T12:36:10.178000Z"
    opportunity.auto_approve_payments = True
    opportunity.auto_approve_visits = False
    opportunity.save()
    make_request(api_client, form_json, user_with_connectid_link)
    visit = UserVisit.objects.get(user=user_with_connectid_link)
    assert not visit.flagged
    assert visit.status == VisitValidationStatus.pending

    # Payment Approval
    update_payment_accrued(opportunity, users=[user_with_connectid_link])
    access = OpportunityAccess.objects.get(user=user_with_connectid_link, opportunity=opportunity)
    completed_work = CompletedWork.objects.get(opportunity_access=access)
    assert completed_work.status == CompletedWorkStatus.pending
    assert access.payment_accrued == 0


def test_auto_approve_payments_approved_visit(
    user_with_connectid_link: User, api_client: APIClient, opportunity: Opportunity
):
    form_json = _create_opp_and_form_json(opportunity, user=user_with_connectid_link)
    form_json["metadata"]["timeEnd"] = "2023-06-07T12:36:10.178000Z"
    opportunity.auto_approve_payments = True
    opportunity.save()
    make_request(api_client, form_json, user_with_connectid_link)
    visit = UserVisit.objects.get(user=user_with_connectid_link)
    visit.status = VisitValidationStatus.approved
    visit.save()
    assert not visit.flagged

    # Payment Approval
    update_payment_accrued(opportunity, users=[user_with_connectid_link])
    access = OpportunityAccess.objects.get(user=user_with_connectid_link, opportunity=opportunity)
    completed_work = CompletedWork.objects.get(opportunity_access=access)
    assert completed_work.status == CompletedWorkStatus.approved
    assert access.payment_accrued == completed_work.payment_accrued


def test_auto_approve_payments_rejected_visit(
    user_with_connectid_link: User, api_client: APIClient, opportunity: Opportunity
):
    form_json = _create_opp_and_form_json(opportunity, user=user_with_connectid_link)
    form_json["metadata"]["timeEnd"] = "2023-06-07T12:36:10.178000Z"
    opportunity.auto_approve_payments = True
    opportunity.save()
    make_request(api_client, form_json, user_with_connectid_link)
    rejected_reason = []
    visit = UserVisit.objects.get(user=user_with_connectid_link)
    visit.status = VisitValidationStatus.rejected
    visit.reason = "rejected"
    rejected_reason.append(visit.reason)
    visit.save()

    duplicate_json = deepcopy(form_json)
    duplicate_json["id"] = str(uuid4())
    make_request(api_client, duplicate_json, user_with_connectid_link)
    visit = UserVisit.objects.get(xform_id=duplicate_json["id"])
    visit.status = VisitValidationStatus.rejected
    visit.reason = "duplicate"
    rejected_reason.append(visit.reason)
    visit.save()

    # Payment Approval
    update_payment_accrued(opportunity, users=[user_with_connectid_link])
    access = OpportunityAccess.objects.get(user=user_with_connectid_link, opportunity=opportunity)
    completed_work = CompletedWork.objects.get(opportunity_access=access)
    assert completed_work.status == CompletedWorkStatus.rejected
    for reason in rejected_reason:
        assert reason in completed_work.reason
    assert access.payment_accrued == completed_work.payment_accrued


def test_auto_approve_payments_approved_visit_task(
    user_with_connectid_link: User, api_client: APIClient, opportunity: Opportunity
):
    form_json = _create_opp_and_form_json(opportunity, user=user_with_connectid_link)
    form_json["metadata"]["timeEnd"] = "2023-06-07T12:36:10.178000Z"
    opportunity.auto_approve_payments = True
    opportunity.save()
    make_request(api_client, form_json, user_with_connectid_link)
    visit = UserVisit.objects.get(user=user_with_connectid_link)
    visit.status = VisitValidationStatus.approved
    visit.save()
    assert not visit.flagged

    # Payment Approval
    bulk_approve_completed_work()
    access = OpportunityAccess.objects.get(user=user_with_connectid_link, opportunity=opportunity)
    completed_work = CompletedWork.objects.get(opportunity_access=access)
    assert completed_work.status == CompletedWorkStatus.approved
    assert access.payment_accrued == completed_work.payment_accrued


def test_auto_approve_payments_rejected_visit_task(
    user_with_connectid_link: User, api_client: APIClient, opportunity: Opportunity
):
    form_json = _create_opp_and_form_json(opportunity, user=user_with_connectid_link)
    form_json["metadata"]["timeEnd"] = "2023-06-07T12:36:10.178000Z"
    opportunity.auto_approve_payments = True
    opportunity.save()
    make_request(api_client, form_json, user_with_connectid_link)
    rejected_reason = []
    visit = UserVisit.objects.get(user=user_with_connectid_link)
    visit.status = VisitValidationStatus.rejected
    visit.reason = "rejected"
    rejected_reason.append(visit.reason)
    visit.save()

    duplicate_json = deepcopy(form_json)
    duplicate_json["id"] = str(uuid4())
    make_request(api_client, duplicate_json, user_with_connectid_link)
    visit = UserVisit.objects.get(xform_id=duplicate_json["id"])
    visit.status = VisitValidationStatus.rejected
    visit.reason = "duplicate"
    rejected_reason.append(visit.reason)
    visit.save()

    # Payment Approval
    bulk_approve_completed_work()
    access = OpportunityAccess.objects.get(user=user_with_connectid_link, opportunity=opportunity)
    completed_work = CompletedWork.objects.get(opportunity_access=access)
    assert completed_work.status == CompletedWorkStatus.rejected
    for reason in rejected_reason:
        assert reason in completed_work.reason
    assert access.payment_accrued == completed_work.payment_accrued


def test_auto_approve_visits_and_payments(
    user_with_connectid_link: User, api_client: APIClient, opportunity: Opportunity
):
    form_json = _create_opp_and_form_json(opportunity, user=user_with_connectid_link)
    form_json["metadata"]["timeEnd"] = "2023-06-07T12:36:10.178000Z"
    opportunity.auto_approve_visits = True
    opportunity.auto_approve_payments = True
    opportunity.save()
    make_request(api_client, form_json, user_with_connectid_link)
    visit = UserVisit.objects.get(user=user_with_connectid_link)
    assert not visit.flagged
    assert visit.status == VisitValidationStatus.approved

    update_payment_accrued(opportunity, users=[user_with_connectid_link])
    access = OpportunityAccess.objects.get(user=user_with_connectid_link, opportunity=opportunity)
    completed_work = CompletedWork.objects.get(opportunity_access=access)
    assert completed_work.status == CompletedWorkStatus.approved
    assert access.payment_accrued == completed_work.payment_accrued


def test_receiver_verification_flags_form_submission(
    user_with_connectid_link: User, api_client: APIClient, opportunity: Opportunity
):
    verification_flags = OpportunityVerificationFlags.objects.get(opportunity=opportunity)
    verification_flags.form_submission_start = datetime.time(hour=10, minute=0)
    verification_flags.form_submission_end = datetime.time(hour=12, minute=0)
    verification_flags.save()

    form_json = _create_opp_and_form_json(opportunity, user=user_with_connectid_link)
    time = datetime.datetime(2024, 4, 17, 10, 0, 0)
    form_json["metadata"]["timeStart"] = time
    form_json["metadata"]["timeEnd"] = time + datetime.timedelta(minutes=10)
    make_request(api_client, form_json, user_with_connectid_link)
    visit = UserVisit.objects.get(user=user_with_connectid_link)
    assert not visit.flagged


def test_receiver_verification_flags_form_submission_start(
    user_with_connectid_link: User, api_client: APIClient, opportunity: Opportunity
):
    verification_flags = OpportunityVerificationFlags.objects.get(opportunity=opportunity)
    verification_flags.form_submission_start = datetime.time(hour=10, minute=0)
    verification_flags.form_submission_end = datetime.time(hour=12, minute=0)
    verification_flags.save()

    form_json = _create_opp_and_form_json(opportunity, user=user_with_connectid_link)
    time = datetime.datetime(2024, 4, 17, 9, 0, 0)
    form_json["metadata"]["timeStart"] = time
    make_request(api_client, form_json, user_with_connectid_link)
    visit = UserVisit.objects.get(user=user_with_connectid_link)
    assert visit.flagged
    assert ["form_submission_period", "Form was submitted before the start time"] in visit.flag_reason.get("flags", [])


def test_receiver_verification_flags_form_submission_end(
    user_with_connectid_link: User, api_client: APIClient, opportunity: Opportunity
):
    verification_flags = OpportunityVerificationFlags.objects.get(opportunity=opportunity)
    verification_flags.form_submission_start = datetime.time(hour=10, minute=0)
    verification_flags.form_submission_end = datetime.time(hour=12, minute=0)
    verification_flags.save()

    form_json = _create_opp_and_form_json(opportunity, user=user_with_connectid_link)
    time = datetime.datetime(2024, 4, 17, 13, 0, 0)
    form_json["metadata"]["timeStart"] = time
    make_request(api_client, form_json, user_with_connectid_link)
    visit = UserVisit.objects.get(user=user_with_connectid_link)
    assert visit.flagged
    assert ["form_submission_period", "Form was submitted after the end time"] in visit.flag_reason.get("flags", [])


def test_receiver_verification_flags_duration(
    user_with_connectid_link: User, api_client: APIClient, opportunity: Opportunity
):
    form_json = _create_opp_and_form_json(opportunity, user=user_with_connectid_link)
    deliver_unit = opportunity.deliver_app.deliver_units.first()
    DeliverUnitFlagRulesFactory(deliver_unit=deliver_unit, opportunity=opportunity, duration=1)

    make_request(api_client, form_json, user_with_connectid_link)
    visit = UserVisit.objects.get(user=user_with_connectid_link)
    assert visit.flagged
    assert ["duration", "The form was completed too quickly."] in visit.flag_reason.get("flags", [])


def test_receiver_verification_flags_check_attachments(
    user_with_connectid_link: User, api_client: APIClient, opportunity: Opportunity
):
    form_json = _create_opp_and_form_json(opportunity, user=user_with_connectid_link)
    deliver_unit = opportunity.deliver_app.deliver_units.first()
    DeliverUnitFlagRulesFactory(deliver_unit=deliver_unit, opportunity=opportunity, duration=0, check_attachments=True)

    make_request(api_client, form_json, user_with_connectid_link)
    visit = UserVisit.objects.get(user=user_with_connectid_link)
    assert visit.flagged
    assert ["attachment_missing", "Form was submitted without attachements."] in visit.flag_reason.get("flags", [])


def test_receiver_verification_flags_form_json_rule(
    user_with_connectid_link: User, api_client: APIClient, opportunity: Opportunity
):
    form_json = _create_opp_and_form_json(opportunity, user=user_with_connectid_link)
    deliver_unit = opportunity.deliver_app.deliver_units.first()
    form_json["form"]["value"] = "123"
    form_json_rule = FormJsonValidationRulesFactory(
        opportunity=opportunity,
        question_path="$.form.value",
        question_value="123",
    )
    form_json_rule.deliver_unit.add(deliver_unit)

    make_request(api_client, form_json, user_with_connectid_link)
    visit = UserVisit.objects.get(user=user_with_connectid_link)
    assert not visit.flagged


def test_receiver_verification_flags_form_json_rule_flagged(
    user_with_connectid_link: User, api_client: APIClient, opportunity: Opportunity
):
    form_json = _create_opp_and_form_json(opportunity, user=user_with_connectid_link)
    deliver_unit = opportunity.deliver_app.deliver_units.first()
    form_json["form"]["value"] = "456"
    form_json_rule = FormJsonValidationRulesFactory(
        opportunity=opportunity,
        question_path="$.form.value",
        question_value="123",
    )
    form_json_rule.deliver_unit.add(deliver_unit)

    make_request(api_client, form_json, user_with_connectid_link)
    visit = UserVisit.objects.get(user=user_with_connectid_link)
    assert visit.flagged
    assert [
        "form_value_not_found",
        f"Form does not satisfy {form_json_rule.name} validation rule.",
    ] in visit.flag_reason.get("flags", [])


def test_receiver_verification_flags_catchment_areas(
    user_with_connectid_link: User, api_client: APIClient, opportunity: Opportunity
):
    verification_flags = OpportunityVerificationFlags.objects.get(opportunity=opportunity)
    verification_flags.catchment_areas = True
    verification_flags.save()

    form_json = _create_opp_and_form_json(opportunity, user=user_with_connectid_link)
    form_json["metadata"]["location"] = None

    access = OpportunityAccess.objects.get(user=user_with_connectid_link, opportunity=opportunity)
    CatchmentAreaFactory(opportunity=opportunity, opportunity_access=access, active=True)

    make_request(api_client, form_json, user_with_connectid_link)
    visit = UserVisit.objects.get(user=user_with_connectid_link)
    assert visit.flagged
    assert ["catchment", "Visit outside worker catchment areas"] in visit.flag_reason.get("flags", [])


@pytest.mark.parametrize("opportunity", [{"managed": True}], indirect=True)
def test_receiver_auto_agree_approved_visit(
    mobile_user_with_connect_link: User, api_client: APIClient, opportunity: Opportunity
):
    assert opportunity.managed
    form_json = get_form_json_for_payment_unit(opportunity.paymentunit_set.all()[0])
    make_request(api_client, form_json, mobile_user_with_connect_link)
    visit = UserVisit.objects.get(user=mobile_user_with_connect_link)
    assert not visit.flagged
    assert visit.status == VisitValidationStatus.approved
    assert visit.review_status == VisitReviewStatus.agree


@pytest.mark.parametrize("opportunity", [{"managed": True}], indirect=True)
def test_receiver_flagged_visit_review_pending(
    mobile_user_with_connect_link: User, api_client: APIClient, opportunity: Opportunity
):
    assert opportunity.managed
    form_json = get_form_json_for_payment_unit(opportunity.paymentunit_set.all()[0])
    form_json["metadata"]["location"] = None
    make_request(api_client, form_json, mobile_user_with_connect_link)
    visit = UserVisit.objects.get(user=mobile_user_with_connect_link)
    assert visit.flagged
    assert visit.status == VisitValidationStatus.pending
    assert visit.review_status == VisitReviewStatus.pending


def get_form_json_for_payment_unit(payment_unit):
    deliver_unit = DeliverUnitFactory(app=payment_unit.opportunity.deliver_app, payment_unit=payment_unit)
    stub = DeliverUnitStubFactory(id=deliver_unit.slug)
    form_json = get_form_json(
        form_block=stub.json,
        domain=deliver_unit.app.cc_domain,
        app_id=deliver_unit.app.cc_app_id,
    )
    return form_json


def _get_form_json(learn_app, module_id, form_block=None):
    form_json = get_form_json(
        form_block=form_block or LearnModuleJsonFactory(id=module_id).json,
        domain=learn_app.cc_domain,
        app_id=learn_app.cc_app_id,
    )
    return form_json


def make_request(api_client, form_json, user, expected_status_code=200):
    add_credentials(api_client, user)
    response = api_client.post("/api/receiver/", data=form_json, format="json")
    assert response.status_code == expected_status_code, response.data
