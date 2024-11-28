import datetime

import pytest
from rest_framework.test import APIClient

from commcare_connect.opportunity.api.serializers import (
    DeliveryProgressSerializer,
    OpportunitySerializer,
    PaymentSerializer,
    UserVisitSerializer,
)
from commcare_connect.opportunity.models import (
    CompletedWorkStatus,
    Opportunity,
    OpportunityAccess,
    OpportunityClaim,
    OpportunityClaimLimit,
    Payment,
    VisitValidationStatus,
)
from commcare_connect.opportunity.tests.factories import (
    AssessmentFactory,
    CompletedModuleFactory,
    CompletedWorkFactory,
    LearnModuleFactory,
    OpportunityAccessFactory,
    OpportunityFactory,
    PaymentUnitFactory,
    UserVisitFactory,
)
from commcare_connect.users.models import User
from commcare_connect.users.tests.factories import ConnectIdUserLinkFactory


def _setup_opportunity_and_access(mobile_user: User, total_budget, end_date, budget_per_visit=10):
    opportunity = OpportunityFactory(
        total_budget=total_budget,
        end_date=end_date,
    )
    PaymentUnitFactory(opportunity=opportunity, amount=budget_per_visit, max_total=100)
    opportunity_access = OpportunityAccessFactory(opportunity=opportunity, user=mobile_user)
    ConnectIdUserLinkFactory(
        user=mobile_user, commcare_username="test@ccc-test.commcarehq.org", domain=opportunity.deliver_app.cc_domain
    )
    return opportunity, opportunity_access


def test_claim_endpoint_success(mobile_user: User, api_client: APIClient):
    opportunity, opportunity_access = _setup_opportunity_and_access(
        mobile_user, total_budget=1000, end_date=datetime.date.today() + datetime.timedelta(days=100)
    )
    api_client.force_authenticate(mobile_user)
    response = api_client.post(f"/api/opportunity/{opportunity.id}/claim")
    assert response.status_code == 201
    claim = OpportunityClaim.objects.filter(opportunity_access=opportunity_access)
    assert claim.exists()


def test_claim_endpoint_budget_exhausted(mobile_user: User, api_client: APIClient):
    opportunity, opportunity_access = _setup_opportunity_and_access(
        mobile_user, total_budget=0, end_date=datetime.date.today() + datetime.timedelta(days=100)
    )
    api_client.force_authenticate(mobile_user)
    response = api_client.post(f"/api/opportunity/{opportunity.id}/claim")
    assert response.status_code == 400
    assert response.data == "Opportunity cannot be claimed. (Budget Exhausted)"


def test_claim_endpoint_end_date_exceeded(mobile_user: User, api_client: APIClient):
    opportunity, opportunity_access = _setup_opportunity_and_access(
        mobile_user, total_budget=1000, end_date=datetime.date.today() - datetime.timedelta(days=100)
    )
    api_client.force_authenticate(mobile_user)
    response = api_client.post(f"/api/opportunity/{opportunity.id}/claim")
    assert response.status_code == 400
    assert response.data == "Opportunity cannot be claimed. (End date reached)"


def test_claim_endpoint_already_claimed_opportunity(mobile_user: User, api_client: APIClient):
    opportunity, opportunity_access = _setup_opportunity_and_access(
        mobile_user, total_budget=1000, end_date=datetime.date.today() + datetime.timedelta(days=100)
    )
    api_client.force_authenticate(mobile_user)
    response = api_client.post(f"/api/opportunity/{opportunity.id}/claim")
    assert response.status_code == 201
    claim = OpportunityClaim.objects.filter(opportunity_access=opportunity_access)
    assert claim.exists()

    response = api_client.post(f"/api/opportunity/{opportunity.id}/claim")
    assert response.status_code == 200
    assert response.data == "Opportunity is already claimed"


def test_claim_endpoint_less_budget_than_visit(mobile_user: User, api_client: APIClient):
    opportunity, opportunity_access = _setup_opportunity_and_access(
        mobile_user,
        total_budget=1,
        end_date=datetime.date.today() + datetime.timedelta(days=100),
        budget_per_visit=2,
    )
    api_client.force_authenticate(mobile_user)
    response = api_client.post(f"/api/opportunity/{opportunity.id}/claim")
    assert response.status_code == 400
    assert response.data == "Opportunity cannot be claimed. (Budget Exhausted)"


def test_claim_endpoint_uneven_visits(mobile_user: User, api_client: APIClient):
    opportunity, opportunity_access = _setup_opportunity_and_access(
        mobile_user,
        total_budget=3,
        end_date=datetime.date.today() + datetime.timedelta(days=100),
        budget_per_visit=2,
    )
    api_client.force_authenticate(mobile_user)
    response = api_client.post(f"/api/opportunity/{opportunity.id}/claim")
    assert response.status_code == 201
    claim = OpportunityClaim.objects.get(opportunity_access=opportunity_access)
    assert claim.opportunityclaimlimit_set.first().max_visits == 1


@pytest.mark.django_db
def test_learn_progress_endpoint(mobile_user: User, api_client: APIClient):
    opportunity, opportunity_access = _setup_opportunity_and_access(
        mobile_user, total_budget=1000, end_date=datetime.date.today() + datetime.timedelta(days=100)
    )
    learn_module = LearnModuleFactory(slug="module_1", app=opportunity.learn_app)
    CompletedModuleFactory(
        module=learn_module, user=mobile_user, opportunity=opportunity, opportunity_access=opportunity_access
    )
    AssessmentFactory(
        user=mobile_user,
        app=opportunity.learn_app,
        opportunity=opportunity,
        opportunity_access=opportunity_access,
        score=100,
        passed=True,
    )
    api_client.force_authenticate(mobile_user)
    response = api_client.get(f"/api/opportunity/{opportunity.id}/learn_progress")
    assert response.status_code == 200
    assert "completed_modules" in response.data
    assert len(response.data["completed_modules"]) == 1
    assert "assessments" in response.data
    assert len(response.data["assessments"]) == 1
    assert list(response.data["completed_modules"][0].keys()) == ["module", "date", "duration"]
    assert list(response.data["assessments"][0].keys()) == ["date", "score", "passing_score", "passed"]


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
def test_opportunity_list_endpoint(
    mobile_user_with_connect_link: User,
    api_client: APIClient,
    opportunity: Opportunity,
):
    api_client.force_authenticate(mobile_user_with_connect_link)
    response = api_client.get("/api/opportunity/")
    assert response.status_code == 200
    assert len(response.data) == 1
    assert response.data[0].keys() == OpportunitySerializer().get_fields().keys()
    payment_units = opportunity.paymentunit_set.all()
    assert response.data[0]["max_visits_per_user"] == sum([pu.max_total for pu in payment_units])
    assert response.data[0]["daily_max_visits_per_user"] == sum([pu.max_daily for pu in payment_units])
    assert response.data[0]["budget_per_visit"] == max([pu.amount for pu in payment_units])
    claim_limits = OpportunityClaimLimit.objects.filter(opportunity_claim__opportunity_access__opportunity=opportunity)
    assert response.data[0]["claim"]["max_payments"] == sum([cl.max_visits for cl in claim_limits])
    verification_flags = opportunity.opportunityverificationflags
    assert response.data[0]["verification_flags"]["form_submission_start"] == str(
        verification_flags.form_submission_start
    )
    assert response.data[0]["verification_flags"]["form_submission_end"] == str(verification_flags.form_submission_end)


def test_delivery_progress_endpoint(
    mobile_user_with_connect_link: User, api_client: APIClient, opportunity: Opportunity
):
    access = OpportunityAccess.objects.get(user=mobile_user_with_connect_link, opportunity=opportunity)
    completed_work = CompletedWorkFactory(opportunity_access=access, status=CompletedWorkStatus.pending)
    UserVisitFactory.create(
        opportunity=opportunity,
        user=mobile_user_with_connect_link,
        status=VisitValidationStatus.pending,
        opportunity_access=access,
        completed_work=completed_work,
    )
    api_client.force_authenticate(mobile_user_with_connect_link)
    response = api_client.get(f"/api/opportunity/{opportunity.id}/delivery_progress")

    assert response.status_code == 200
    assert response.data.keys() == DeliveryProgressSerializer().get_fields().keys()
    assert len(response.data["deliveries"]) == 1
    assert len(response.data["payments"]) == 0
    assert response.data["deliveries"][0].keys() == UserVisitSerializer().get_fields().keys()

    Payment.objects.create(amount=10, date_paid=datetime.date.today(), opportunity_access=access)
    response = api_client.get(f"/api/opportunity/{opportunity.id}/delivery_progress")
    assert response.status_code == 200
    assert len(response.data["payments"]) == 1
    assert response.data["payments"][0].keys() == PaymentSerializer().get_fields().keys()


def test_confirm_payment(mobile_user_with_connect_link: User, api_client: APIClient, opportunity: Opportunity):
    access = OpportunityAccess.objects.get(user=mobile_user_with_connect_link, opportunity=opportunity)
    api_client.force_authenticate(mobile_user_with_connect_link)
    payment = Payment.objects.create(amount=10, date_paid=datetime.date.today(), opportunity_access=access)
    response = api_client.get(f"/api/opportunity/{opportunity.id}/delivery_progress")
    assert response.status_code == 200
    assert len(response.data["payments"]) == 1
    assert response.data["payments"][0]["confirmed"] is False

    # test confirmation
    response = api_client.post(f"/api/payment/{payment.pk}/confirm", {"confirmed": "true"})
    assert response.status_code == 200
    response = api_client.get(f"/api/opportunity/{opportunity.id}/delivery_progress")
    assert response.status_code == 200
    payment_data = response.data["payments"][0]
    assert payment_data["confirmed"] is True
    assert payment_data["confirmation_date"] is not None

    # test undo
    response = api_client.post(f"/api/payment/{payment.pk}/confirm", {"confirmed": "false"})
    assert response.status_code == 200
    response = api_client.get(f"/api/opportunity/{opportunity.id}/delivery_progress")
    assert response.status_code == 200
    payment_data = response.data["payments"][0]
    assert payment_data["confirmed"] is False
