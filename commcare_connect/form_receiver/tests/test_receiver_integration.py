import datetime
import importlib
from copy import deepcopy
from datetime import timedelta
from http import HTTPStatus
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.utils.timezone import now
from rest_framework.test import APIClient

from commcare_connect.form_receiver.processor import update_completed_learn_date
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
    PaymentUnit,
    UserVisit,
    VisitReviewStatus,
    VisitValidationStatus,
)
from commcare_connect.opportunity.tasks import bulk_approve_completed_work
from commcare_connect.opportunity.tests.factories import (
    CatchmentAreaFactory,
    CompletedModuleFactory,
    CompletedWorkFactory,
    DeliverUnitFactory,
    DeliverUnitFlagRulesFactory,
    FormJsonValidationRulesFactory,
    LearnModuleFactory,
    OpportunityAccessFactory,
    OpportunityClaimFactory,
    PaymentUnitFactory,
    UserVisitFactory,
)
from commcare_connect.opportunity.tests.helpers import validate_saved_fields
from commcare_connect.opportunity.visit_import import update_payment_accrued
from commcare_connect.users.models import User


@pytest.mark.django_db
def test_form_receiver_learn_module(
    mobile_user_with_connect_link: User, api_client: APIClient, opportunity: Opportunity
):
    module_id = "learn_module_1"
    oauth_application = opportunity.hq_server.oauth_application
    form_json = _get_form_json(opportunity.learn_app, module_id)
    assert CompletedModule.objects.count() == 0
    learn_module = LearnModuleFactory(app=opportunity.learn_app, slug=module_id)
    make_request(api_client, form_json, mobile_user_with_connect_link, oauth_application=oauth_application)

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
    oauth_application = opportunity.hq_server.oauth_application
    form_json = _get_form_json(opportunity.learn_app, module.id, module.json)
    assert CompletedModule.objects.count() == 0

    make_request(api_client, form_json, mobile_user_with_connect_link, oauth_application=oauth_application)
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


@pytest.mark.parametrize(
    "module_count, initial_date_offset, subsequent_date_offset",
    [
        (2, 5, 2),  # Test with 2 modules, initial submission 5 days ago, subsequent submission 2 days after current
    ],
)
def test_form_receiver_multiple_module_submissions(
    mobile_user_with_connect_link: User,
    api_client: APIClient,
    opportunity: Opportunity,
    module_count: int,
    initial_date_offset: int,
    subsequent_date_offset: int,
):
    modules = [LearnModuleJsonFactory() for _ in range(module_count)]
    oauth_application = opportunity.hq_server.oauth_application
    current = now()
    past_date = current - timedelta(days=initial_date_offset)
    future_date = current + timedelta(days=subsequent_date_offset)

    # First submissions for all modules
    for module in modules:
        form_json = _get_form_json(opportunity.learn_app, module.id, module.json)
        form_json["received_on"] = past_date
        make_request(api_client, form_json, mobile_user_with_connect_link, oauth_application=oauth_application)

    # Subsequent submissions
    for module in modules:
        form_json = _get_form_json(opportunity.learn_app, module.id, module.json)
        form_json["received_on"] = future_date
        form_json["id"] = str(uuid4())  # Change form ID to simulate a new submission
        make_request(api_client, form_json, mobile_user_with_connect_link, oauth_application=oauth_application)

    assert CompletedModule.objects.count() == module_count * 2  # Initial + subsequent submissions
    access = OpportunityAccess.objects.get(opportunity=opportunity, user=mobile_user_with_connect_link)
    assert access.unique_completed_modules.count() == module_count

    for module in modules:
        assert CompletedModule.objects.filter(
            module__slug=module.id,
            date=past_date,
        ).exists()
        assert CompletedModule.objects.filter(
            module__slug=module.id,
            date=future_date,
        ).exists()

    # Test integrity error for duplicate submissions keeping the id same.
    with patch("commcare_connect.form_receiver.views.logger") as mock_logger:
        form_json = _get_form_json(opportunity.learn_app, modules[0].id, modules[0].json)
        form_json["received_on"] = past_date
        make_request(
            api_client, form_json, mobile_user_with_connect_link, HTTPStatus.OK, oauth_application=oauth_application
        )
        xform_id = form_json["id"]
        mock_logger.info.assert_any_call(f"Learn Module is already completed with form ID: {xform_id}.")


@pytest.mark.django_db
def test_form_receiver_assessment(
    mobile_user_with_connect_link: User, api_client: APIClient, opportunity: Opportunity
):
    passing_score = opportunity.learn_app.passing_score
    oauth_application = opportunity.hq_server.oauth_application
    score = passing_score + 5
    assessment = AssessmentStubFactory(score=score).json
    form_json = get_form_json(
        form_block=assessment,
        domain=opportunity.learn_app.cc_domain,
        app_id=opportunity.learn_app.cc_app_id,
    )
    assert Assessment.objects.count() == 0

    make_request(api_client, form_json, mobile_user_with_connect_link, oauth_application=oauth_application)
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
    oauth_application = opportunity.hq_server.oauth_application
    stub = DeliverUnitStubFactory(id=deliver_unit.slug)
    form_json = get_form_json(
        form_block=stub.json,
        domain=deliver_unit.app.cc_domain,
        app_id=deliver_unit.app.cc_app_id,
    )
    assert UserVisit.objects.filter(user=mobile_user_with_connect_link).count() == 0

    make_request(api_client, form_json, mobile_user_with_connect_link, oauth_application=oauth_application)
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
    oauth_application = opportunity.hq_server.oauth_application
    form_json = _create_opp_and_form_json(opportunity, user=user_with_connectid_link, daily_max_per_user=0)
    assert UserVisit.objects.filter(user=user_with_connectid_link).count() == 0
    make_request(api_client, form_json, user_with_connectid_link, oauth_application=oauth_application)
    assert UserVisit.objects.filter(user=user_with_connectid_link).count() == 1
    visit = UserVisit.objects.get(user=user_with_connectid_link)
    assert visit.status == VisitValidationStatus.over_limit


@pytest.mark.django_db
@pytest.mark.parametrize("paymentunit_options", [pytest.param({"max_daily": 2})])
def test_receiver_deliver_form_max_visits_reached(
    mobile_user_with_connect_link: User, api_client: APIClient, opportunity: Opportunity
):
    oauth_application = opportunity.hq_server.oauth_application

    def submit_form_for_random_entity(form_json):
        duplicate_json = deepcopy(form_json)
        duplicate_json["form"]["deliver"]["entity_id"] = str(uuid4())
        make_request(api_client, duplicate_json, mobile_user_with_connect_link, oauth_application=oauth_application)

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
    oauth_application = opportunity.hq_server.oauth_application
    form_json = _create_opp_and_form_json(
        opportunity, user=user_with_connectid_link, end_date=datetime.date.today() - datetime.timedelta(days=100)
    )
    assert UserVisit.objects.filter(user=user_with_connectid_link).count() == 0
    assert CompletedWork.objects.count() == 0
    make_request(api_client, form_json, user_with_connectid_link, oauth_application=oauth_application)
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
    oauth_application = opportunity.hq_server.oauth_application
    form_json = _create_opp_and_form_json(
        opportunity, user=user_with_connectid_link, end_date=datetime.date.today() + datetime.timedelta(days=100)
    )
    assert UserVisit.objects.filter(user=user_with_connectid_link).count() == 0
    make_request(api_client, form_json, user_with_connectid_link, oauth_application=oauth_application)
    assert UserVisit.objects.filter(user=user_with_connectid_link).count() == 1
    visit = UserVisit.objects.get(user=user_with_connectid_link)
    assert visit.status == VisitValidationStatus.trial
    assert CompletedWork.objects.count() == 0


def test_receiver_duplicate(user_with_connectid_link: User, api_client: APIClient, opportunity: Opportunity):
    oauth_application = opportunity.hq_server.oauth_application
    form_json = _create_opp_and_form_json(opportunity, user=user_with_connectid_link)
    make_request(api_client, form_json, user_with_connectid_link, oauth_application=oauth_application)
    visit = UserVisit.objects.get(user=user_with_connectid_link)
    assert visit.status == VisitValidationStatus.approved
    duplicate_json = deepcopy(form_json)
    duplicate_json["id"] = str(uuid4())
    make_request(api_client, duplicate_json, user_with_connectid_link, oauth_application=oauth_application)
    visit = UserVisit.objects.get(xform_id=duplicate_json["id"])
    assert visit.status == VisitValidationStatus.duplicate
    assert ["duplicate", "A beneficiary with the same identifier already exists"] in visit.flag_reason.get("flags", [])


def test_flagged_form(user_with_connectid_link: User, api_client: APIClient, opportunity: Opportunity):
    # The mock data for form fails with duration flag
    oauth_application = opportunity.hq_server.oauth_application
    form_json = _create_opp_and_form_json(opportunity, user=user_with_connectid_link)
    deliver_unit = opportunity.deliver_app.deliver_units.first()
    DeliverUnitFlagRulesFactory(deliver_unit=deliver_unit, opportunity=opportunity, duration=1)
    make_request(api_client, form_json, user_with_connectid_link, oauth_application=oauth_application)
    visit = UserVisit.objects.get(user=user_with_connectid_link)
    assert visit.status == VisitValidationStatus.pending
    assert visit.flagged
    assert len(visit.flag_reason.get("flags", []))


def test_auto_approve_unflagged_visits(
    user_with_connectid_link: User, api_client: APIClient, opportunity: Opportunity
):
    oauth_application = opportunity.hq_server.oauth_application
    form_json = _create_opp_and_form_json(opportunity, user=user_with_connectid_link)
    form_json["metadata"]["timeEnd"] = "2023-06-07T12:36:10.178000Z"
    opportunity.auto_approve_visits = True
    opportunity.save()
    make_request(api_client, form_json, user_with_connectid_link, oauth_application=oauth_application)
    visit = UserVisit.objects.get(user=user_with_connectid_link)
    assert not visit.flagged
    assert visit.status == VisitValidationStatus.approved


def test_auto_approve_flagged_visits(user_with_connectid_link: User, api_client: APIClient, opportunity: Opportunity):
    form_json = _create_opp_and_form_json(opportunity, user=user_with_connectid_link)
    opportunity.auto_approve_visits = True
    opportunity.save()
    oauth_application = opportunity.hq_server.oauth_application
    deliver_unit = opportunity.deliver_app.deliver_units.first()
    DeliverUnitFlagRulesFactory(deliver_unit=deliver_unit, opportunity=opportunity, duration=1)
    make_request(api_client, form_json, user_with_connectid_link, oauth_application=oauth_application)
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
    oauth_application = opportunity.hq_server.oauth_application
    deliver_unit = opportunity.deliver_app.deliver_units.first()
    DeliverUnitFlagRulesFactory(deliver_unit=deliver_unit, opportunity=opportunity, duration=1)
    make_request(api_client, form_json, user_with_connectid_link, oauth_application=oauth_application)
    visit = UserVisit.objects.get(user=user_with_connectid_link)
    assert visit.flagged
    assert visit.status == VisitValidationStatus.pending

    # No Payment Approval
    update_payment_accrued(opportunity, users=[user_with_connectid_link])
    access = OpportunityAccess.objects.get(user=user_with_connectid_link, opportunity=opportunity)
    completed_work = CompletedWork.objects.get(opportunity_access=access)
    assert completed_work.status == CompletedWorkStatus.pending
    assert access.payment_accrued == 0
    validate_saved_fields(completed_work)


def test_auto_approve_payments_unflagged_visit(
    user_with_connectid_link: User, api_client: APIClient, opportunity: Opportunity
):
    form_json = _create_opp_and_form_json(opportunity, user=user_with_connectid_link)
    form_json["metadata"]["timeEnd"] = "2023-06-07T12:36:10.178000Z"
    opportunity.auto_approve_payments = True
    opportunity.auto_approve_visits = False
    opportunity.save()
    oauth_application = opportunity.hq_server.oauth_application
    make_request(api_client, form_json, user_with_connectid_link, oauth_application=oauth_application)
    visit = UserVisit.objects.get(user=user_with_connectid_link)
    assert not visit.flagged
    assert visit.status == VisitValidationStatus.pending

    # Payment Approval
    update_payment_accrued(opportunity, users=[user_with_connectid_link])
    access = OpportunityAccess.objects.get(user=user_with_connectid_link, opportunity=opportunity)
    completed_work = CompletedWork.objects.get(opportunity_access=access)
    assert completed_work.status == CompletedWorkStatus.pending
    assert access.payment_accrued == 0
    validate_saved_fields(completed_work)


def test_auto_approve_payments_approved_visit(
    user_with_connectid_link: User, api_client: APIClient, opportunity: Opportunity
):
    form_json = _create_opp_and_form_json(opportunity, user=user_with_connectid_link)
    form_json["metadata"]["timeEnd"] = "2023-06-07T12:36:10.178000Z"
    opportunity.auto_approve_payments = True
    opportunity.save()
    oauth_application = opportunity.hq_server.oauth_application
    make_request(api_client, form_json, user_with_connectid_link, oauth_application=oauth_application)
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
    validate_saved_fields(completed_work)


@pytest.mark.parametrize(
    "update_func, args_required", [(update_payment_accrued, True), (bulk_approve_completed_work, False)]
)
def test_auto_approve_payments_rejected_visit_functions(
    user_with_connectid_link: User, api_client: APIClient, opportunity: Opportunity, update_func, args_required
):
    form_json = _create_opp_and_form_json(opportunity, user=user_with_connectid_link)
    form_json["metadata"]["timeEnd"] = "2023-06-07T12:36:10.178000Z"
    opportunity.auto_approve_payments = True
    opportunity.save()
    oauth_application = opportunity.hq_server.oauth_application
    make_request(api_client, form_json, user_with_connectid_link, oauth_application=oauth_application)
    rejected_reason = []
    visit = UserVisit.objects.get(user=user_with_connectid_link)
    visit.status = VisitValidationStatus.rejected
    visit.reason = "rejected"
    rejected_reason.append(visit.reason)
    visit.save()

    # Payment Approval
    args = (opportunity, [user_with_connectid_link]) if args_required else ()
    update_func(*args)

    access = OpportunityAccess.objects.get(user=user_with_connectid_link, opportunity=opportunity)
    completed_work = CompletedWork.objects.get(opportunity_access=access)
    assert completed_work.status == CompletedWorkStatus.rejected
    for reason in rejected_reason:
        assert reason in completed_work.reason
    assert access.payment_accrued == completed_work.payment_accrued
    validate_saved_fields(completed_work)


def test_auto_approve_payments_approved_visit_task(
    user_with_connectid_link: User, api_client: APIClient, opportunity: Opportunity
):
    form_json = _create_opp_and_form_json(opportunity, user=user_with_connectid_link)
    form_json["metadata"]["timeEnd"] = "2023-06-07T12:36:10.178000Z"
    opportunity.auto_approve_payments = True
    opportunity.save()
    oauth_application = opportunity.hq_server.oauth_application
    make_request(api_client, form_json, user_with_connectid_link, oauth_application=oauth_application)
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
    validate_saved_fields(completed_work)


def test_auto_approve_visits_and_payments(
    user_with_connectid_link: User, api_client: APIClient, opportunity: Opportunity
):
    form_json = _create_opp_and_form_json(opportunity, user=user_with_connectid_link)
    form_json["metadata"]["timeEnd"] = "2023-06-07T12:36:10.178000Z"
    opportunity.auto_approve_visits = True
    opportunity.auto_approve_payments = True
    opportunity.save()
    oauth_application = opportunity.hq_server.oauth_application
    make_request(api_client, form_json, user_with_connectid_link, oauth_application=oauth_application)
    visit = UserVisit.objects.get(user=user_with_connectid_link)
    assert not visit.flagged
    assert visit.status == VisitValidationStatus.approved

    update_payment_accrued(opportunity, users=[user_with_connectid_link])
    access = OpportunityAccess.objects.get(user=user_with_connectid_link, opportunity=opportunity)
    completed_work = CompletedWork.objects.get(opportunity_access=access)
    assert completed_work.status == CompletedWorkStatus.approved
    assert access.payment_accrued == completed_work.payment_accrued
    validate_saved_fields(completed_work)


@pytest.mark.parametrize(
    "opportunity",
    [
        {
            "verification_flags": {
                "form_submission_start": datetime.time(10, 0),
                "form_submission_end": datetime.time(14, 0),
            }
        }
    ],
    indirect=True,
)
@pytest.mark.parametrize(
    "submission_time_hour, expected_message",
    [
        (11, None),
        (9, "Form was submitted before the start time"),
        (15, "Form was submitted after the end time"),
    ],
)
def test_reciever_verification_flags_form_submission(
    user_with_connectid_link: User,
    api_client: APIClient,
    opportunity: Opportunity,
    submission_time_hour,
    expected_message,
):
    form_json = _create_opp_and_form_json(opportunity, user=user_with_connectid_link)
    submission_time = datetime.datetime(2024, 5, 17, hour=submission_time_hour, minute=0)
    form_json["metadata"]["timeStart"] = submission_time
    oauth_application = opportunity.hq_server.oauth_application
    make_request(api_client, form_json, user_with_connectid_link, oauth_application=oauth_application)

    visit = UserVisit.objects.get(user=user_with_connectid_link)

    # Assert based on the expected message
    if expected_message is None:
        assert not visit.flagged
    else:
        assert visit.flagged
        assert ["form_submission_period", expected_message] in visit.flag_reason.get("flags", [])


def test_receiver_verification_flags_duration(
    user_with_connectid_link: User, api_client: APIClient, opportunity: Opportunity
):
    form_json = _create_opp_and_form_json(opportunity, user=user_with_connectid_link)
    deliver_unit = opportunity.deliver_app.deliver_units.first()
    DeliverUnitFlagRulesFactory(deliver_unit=deliver_unit, opportunity=opportunity, duration=1)
    oauth_application = opportunity.hq_server.oauth_application
    make_request(api_client, form_json, user_with_connectid_link, oauth_application=oauth_application)
    visit = UserVisit.objects.get(user=user_with_connectid_link)
    assert visit.flagged
    assert ["duration", "The form was completed too quickly."] in visit.flag_reason.get("flags", [])


def test_receiver_verification_flags_check_attachments(
    user_with_connectid_link: User, api_client: APIClient, opportunity: Opportunity
):
    form_json = _create_opp_and_form_json(opportunity, user=user_with_connectid_link)
    deliver_unit = opportunity.deliver_app.deliver_units.first()
    DeliverUnitFlagRulesFactory(deliver_unit=deliver_unit, opportunity=opportunity, duration=0, check_attachments=True)
    oauth_application = opportunity.hq_server.oauth_application
    make_request(api_client, form_json, user_with_connectid_link, oauth_application=oauth_application)
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
    oauth_application = opportunity.hq_server.oauth_application
    make_request(api_client, form_json, user_with_connectid_link, oauth_application=oauth_application)
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
    oauth_application = opportunity.hq_server.oauth_application
    make_request(api_client, form_json, user_with_connectid_link, oauth_application=oauth_application)
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
    oauth_application = opportunity.hq_server.oauth_application
    make_request(api_client, form_json, user_with_connectid_link, oauth_application=oauth_application)
    visit = UserVisit.objects.get(user=user_with_connectid_link)
    assert visit.flagged
    assert ["catchment", "Visit outside worker catchment areas"] in visit.flag_reason.get("flags", [])


@pytest.mark.parametrize("opportunity", [{"opp_options": {"managed": True}}], indirect=True)
def test_approve_rejected_visit(mobile_user_with_connect_link: User, api_client: APIClient, opportunity: Opportunity):
    assert opportunity.managed
    access = OpportunityAccessFactory(opportunity=opportunity, user=mobile_user_with_connect_link)
    payment_unit = PaymentUnitFactory(opportunity=opportunity, org_amount=2)
    deliver_unit = DeliverUnitFactory(app=payment_unit.opportunity.deliver_app, payment_unit=payment_unit)
    completed_work = CompletedWorkFactory(
        opportunity_access=access, status=CompletedWorkStatus.pending, payment_unit=payment_unit
    )
    visit = UserVisitFactory(
        user=mobile_user_with_connect_link,
        opportunity_access=access,
        opportunity=opportunity,
        completed_work=completed_work,
        status=VisitValidationStatus.rejected,
        review_status=VisitReviewStatus.pending,
        deliver_unit=deliver_unit,
    )

    update_payment_accrued(opportunity, [mobile_user_with_connect_link.id])
    completed_work = CompletedWork.objects.get(id=visit.completed_work_id)

    assert visit.status == VisitValidationStatus.rejected
    assert completed_work.status == CompletedWorkStatus.rejected

    visit.status = VisitValidationStatus.approved
    visit.review_status = VisitReviewStatus.agree
    visit.save()

    update_payment_accrued(opportunity, [mobile_user_with_connect_link.id])
    completed_work.refresh_from_db()

    assert visit.status == VisitValidationStatus.approved
    assert completed_work.status == CompletedWorkStatus.approved


@pytest.mark.parametrize("opportunity", [{"opp_options": {"managed": True}}], indirect=True)
@pytest.mark.parametrize(
    "visit_status, review_status",
    [
        (VisitValidationStatus.approved, VisitReviewStatus.agree),
        (VisitValidationStatus.pending, VisitReviewStatus.pending),
    ],
)
def test_receiver_visit_review_status(
    mobile_user_with_connect_link: User, api_client: APIClient, opportunity: Opportunity, visit_status, review_status
):
    assert opportunity.managed
    form_json = get_form_json_for_payment_unit(opportunity.paymentunit_set.first())
    if visit_status != VisitValidationStatus.approved:
        form_json["metadata"]["location"] = None
    oauth_application = opportunity.hq_server.oauth_application
    make_request(api_client, form_json, mobile_user_with_connect_link, oauth_application=oauth_application)
    visit = UserVisit.objects.get(user=mobile_user_with_connect_link)
    if visit_status != VisitValidationStatus.approved:
        assert visit.flagged
    assert visit.status == visit_status
    assert visit.review_status == review_status


@pytest.mark.parametrize("opportunity", [{"opp_options": {"managed": True}}], indirect=True)
def test_receiver_duplicate_managed_opportunity(
    user_with_connectid_link: User, api_client: APIClient, opportunity: Opportunity
):
    form_json = _create_opp_and_form_json(opportunity, user=user_with_connectid_link)
    oauth_application = opportunity.hq_server.oauth_application
    make_request(api_client, form_json, user_with_connectid_link, oauth_application=oauth_application)
    access = OpportunityAccess.objects.get(opportunity=opportunity, user=user_with_connectid_link)
    payment_unit = PaymentUnit.objects.get(opportunity=opportunity)
    visit = UserVisit.objects.get(user=user_with_connectid_link)
    assert visit.status == VisitValidationStatus.approved
    assert access.payment_accrued == payment_unit.amount

    duplicate_json = deepcopy(form_json)
    duplicate_json["id"] = str(uuid4())
    make_request(api_client, duplicate_json, user_with_connectid_link, oauth_application=oauth_application)
    visit = UserVisit.objects.get(xform_id=duplicate_json["id"])
    access.refresh_from_db()
    assert visit.status == VisitValidationStatus.duplicate
    assert access.payment_accrued == payment_unit.amount
    assert ["duplicate", "A beneficiary with the same identifier already exists"] in visit.flag_reason.get("flags", [])


@pytest.mark.parametrize(
    "opportunity, paymentunit_options, visit_status",
    [
        ({}, {"start_date": now().date()}, VisitValidationStatus.approved),
        ({}, {"start_date": now() + datetime.timedelta(days=2)}, VisitValidationStatus.trial),
        ({}, {"end_date": now().date()}, VisitValidationStatus.approved),
        ({}, {"end_date": now() - datetime.timedelta(days=2)}, VisitValidationStatus.over_limit),
        ({"opp_options": {"start_date": now().date()}}, {}, VisitValidationStatus.approved),
        ({"opp_options": {"start_date": now() + datetime.timedelta(days=2)}}, {}, VisitValidationStatus.trial),
        ({"opp_options": {"end_date": now().date()}}, {}, VisitValidationStatus.approved),
    ],
    indirect=["opportunity"],
)
def test_receiver_visit_payment_unit_dates(
    mobile_user_with_connect_link: User, api_client: APIClient, opportunity: Opportunity, visit_status
):
    form_json = get_form_json_for_payment_unit(opportunity.paymentunit_set.first())
    form_json["metadata"]["timeStart"] = now() - datetime.timedelta(minutes=2)
    oauth_application = opportunity.hq_server.oauth_application
    make_request(api_client, form_json, mobile_user_with_connect_link, oauth_application=oauth_application)
    visit = UserVisit.objects.get(user=mobile_user_with_connect_link)
    assert visit.status == visit_status


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


def make_request(api_client, form_json, user, expected_status_code=200, oauth_application=None):
    add_credentials(api_client, user, oauth_application)
    response = api_client.post("/api/receiver/", data=form_json, format="json")
    assert response.status_code == expected_status_code, response.data


@pytest.mark.django_db
def test_receiver_same_visit_twice(
    mobile_user_with_connect_link: User, api_client: APIClient, opportunity: Opportunity
):
    payment_units = opportunity.paymentunit_set.all()
    oauth_application = opportunity.hq_server.oauth_application
    form_json1 = get_form_json_for_payment_unit(payment_units[0])
    form_json2 = deepcopy(form_json1)
    make_request(api_client, form_json1, mobile_user_with_connect_link, oauth_application=oauth_application)
    make_request(
        api_client, form_json2, mobile_user_with_connect_link, HTTPStatus.OK, oauth_application=oauth_application
    )
    user_visits = UserVisit.objects.filter(user=mobile_user_with_connect_link)
    assert user_visits.count() == 1


def create_learn_module_data(opportunity, mobile_user, access):
    today = now()
    two_days_ago = today - timedelta(days=2)
    three_days_ago = today - timedelta(days=3)
    tomorrow = today + timedelta(days=1)
    future_date = today + timedelta(days=4)

    module1 = LearnModuleFactory(app=opportunity.learn_app)
    module2 = LearnModuleFactory(app=opportunity.learn_app)
    module3 = LearnModuleFactory(app=opportunity.learn_app)

    completions = [
        # module1 completions
        (module1, two_days_ago),
        (module1, future_date),
        (module1, three_days_ago),
        # module2 completion
        (module2, today),
        # module3 completions
        (module3, tomorrow),
        (module3, future_date),
    ]

    for module, date in completions:
        CompletedModuleFactory(
            user=mobile_user,
            opportunity=opportunity,
            module=module,
            date=date,
            opportunity_access=access,
            xform_id=uuid4(),
        )

    return {
        "today": today,
        "two_days_ago": two_days_ago,
        "three_days_ago": three_days_ago,
        "tomorrow": tomorrow,
        "future_date": future_date,
    }


@pytest.mark.django_db
def test_update_completed_learn_date(opportunity, mobile_user):
    access = OpportunityAccess.objects.get(user=mobile_user, opportunity=opportunity)

    dates = create_learn_module_data(opportunity, mobile_user, access)
    assert LearnModule.objects.filter(app=opportunity.learn_app).count() == 3

    update_completed_learn_date(access)

    access.refresh_from_db()
    assert access.completed_learn_date == dates["tomorrow"]


@pytest.mark.django_db
def test_update_completed_learn_date_migration(opportunity, mobile_user):
    access = OpportunityAccess.objects.get(user=mobile_user, opportunity=opportunity)
    access2 = OpportunityAccessFactory(opportunity=opportunity)

    dates = create_learn_module_data(opportunity, mobile_user, access)

    access.date_learn_started = dates["three_days_ago"]
    access.save()

    UserVisitFactory(
        user=mobile_user,
        opportunity=opportunity,
        opportunity_access=access,
        visit_date=dates["future_date"] + timedelta(days=1),
    )

    migration_module = importlib.import_module(
        "commcare_connect.opportunity.migrations.0075_opportunityaccess_completed_learn_date_and_more"
    )
    back_fill_completed_learn_date = migration_module._back_fill_completed_learn_date

    back_fill_completed_learn_date(Opportunity, OpportunityAccess, CompletedModule, UserVisit)

    access.refresh_from_db()
    access2.refresh_from_db()

    assert access.last_active == dates["future_date"] + timedelta(days=1)
    assert access.completed_learn_date == dates["tomorrow"]
    assert access2.completed_learn_date is None
    assert access2.last_active is None
