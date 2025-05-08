from datetime import timedelta
from decimal import Decimal
from http import HTTPStatus

import pytest
from django.test import Client
from django.urls import reverse
from django.utils.timezone import now

from commcare_connect.opportunity.helpers import get_opportunity_list_data
from commcare_connect.opportunity.models import (
    Opportunity,
    OpportunityAccess,
    OpportunityClaimLimit,
    UserInviteStatus,
    VisitValidationStatus,
)
from commcare_connect.opportunity.tests.factories import (
    OpportunityAccessFactory,
    OpportunityClaimFactory,
    OpportunityClaimLimitFactory,
    PaymentFactory,
    PaymentUnitFactory,
    UserInviteFactory,
    UserVisitFactory,
)
from commcare_connect.organization.models import Organization
from commcare_connect.program.tests.factories import ManagedOpportunityFactory, ProgramFactory
from commcare_connect.users.models import User
from commcare_connect.users.tests.factories import MembershipFactory


@pytest.mark.django_db
def test_add_budget_existing_users(
    organization: Organization, org_user_member: User, opportunity: Opportunity, mobile_user: User, client: Client
):
    # access = OpportunityAccessFactory(user=user, opportunity=opportunity, accepted=True)
    # claim = OpportunityClaimFactory(end_date=opportunity.end_date, opportunity_access=access)
    payment_units = PaymentUnitFactory.create_batch(2, opportunity=opportunity, amount=1, max_total=100)
    budget_per_user = sum([p.max_total * p.amount for p in payment_units])
    opportunity.total_budget = budget_per_user

    opportunity.organization = organization
    opportunity.save()
    access = OpportunityAccess.objects.get(opportunity=opportunity, user=mobile_user)
    claim = OpportunityClaimFactory(opportunity_access=access, end_date=opportunity.end_date)
    ocl = OpportunityClaimLimitFactory(opportunity_claim=claim, payment_unit=payment_units[0], max_visits=10)
    assert opportunity.total_budget == 200
    assert opportunity.claimed_budget == 10

    url = reverse("opportunity:add_budget_existing_users", args=(organization.slug, opportunity.pk))
    client.force_login(org_user_member)
    response = client.post(url, data=dict(selected_users=[claim.id], additional_visits=5))
    assert response.status_code == 302
    opportunity = Opportunity.objects.get(pk=opportunity.pk)
    assert opportunity.total_budget == 205
    assert opportunity.claimed_budget == 15
    assert OpportunityClaimLimit.objects.get(pk=ocl.pk).max_visits == 15


def test_add_budget_existing_users_for_managed_opportunity(
    client, program_manager_org, org_user_admin, organization, mobile_user
):
    payment_per_visit = 5
    org_pay_per_visit = 1
    max_visits_per_user = 10

    budget_per_user = max_visits_per_user * (payment_per_visit + org_pay_per_visit)
    initial_total_budget = budget_per_user * 2

    program = ProgramFactory(organization=program_manager_org, budget=200)
    opportunity = ManagedOpportunityFactory(
        program=program,
        organization=organization,
        total_budget=initial_total_budget,
        org_pay_per_visit=org_pay_per_visit,
    )
    payment_unit = PaymentUnitFactory(opportunity=opportunity, max_total=max_visits_per_user, amount=payment_per_visit)
    access = OpportunityAccessFactory(opportunity=opportunity, user=mobile_user)
    claim = OpportunityClaimFactory(opportunity_access=access, end_date=opportunity.end_date)
    claim_limit = OpportunityClaimLimitFactory(
        opportunity_claim=claim, payment_unit=payment_unit, max_visits=max_visits_per_user
    )

    assert opportunity.total_budget == initial_total_budget
    assert opportunity.claimed_budget == budget_per_user

    url = reverse("opportunity:add_budget_existing_users", args=(opportunity.organization.slug, opportunity.pk))
    client.force_login(org_user_admin)

    additional_visits = 10
    # Budget calculation breakdown: opp_budget=120 Initial_claimed: 60 increase: 60 Final: 120 - Still under opp_budget

    budget_increase = (payment_per_visit + org_pay_per_visit) * additional_visits
    expected_claimed_budget = budget_per_user + budget_increase

    response = client.post(url, data={"selected_users": [claim.id], "additional_visits": additional_visits})
    assert response.status_code == HTTPStatus.FOUND

    opportunity.refresh_from_db()
    claim_limit.refresh_from_db()

    assert opportunity.total_budget == initial_total_budget
    assert opportunity.claimed_budget == expected_claimed_budget
    assert claim_limit.max_visits == max_visits_per_user + additional_visits

    additional_visits = 1
    # Budget calculation breakdown: Previous: claimed 120 increase: 6 final: 126 - Exceeds opp_budget budget of 120

    response = client.post(url, data={"selected_users": [claim.id], "additional_visits": additional_visits})
    assert response.status_code == HTTPStatus.OK
    form = response.context["form"]
    assert "additional_visits" in form.errors
    assert form.errors["additional_visits"][0] == "Additional visits exceed the opportunity budget."


@pytest.mark.parametrize(
    "opportunity",
    [
        {"opp_options": {"managed": True}},
        {"opp_options": {"managed": False}},
    ],
    indirect=True,
)
@pytest.mark.django_db
def test_approve_visit(
    client: Client,
    organization,
    opportunity,
):
    justification = "Justification test."
    access = OpportunityAccessFactory(opportunity=opportunity)
    visit = UserVisitFactory.create(
        opportunity=opportunity, opportunity_access=access, flagged=True, status=VisitValidationStatus.pending
    )
    user = MembershipFactory.create(organization=opportunity.organization).user
    approve_url = reverse("opportunity:approve_visit", args=(opportunity.organization.slug, visit.id))
    client.force_login(user)
    response = client.post(approve_url, {"justification": justification}, follow=True)
    visit.refresh_from_db()
    assert visit.status == VisitValidationStatus.approved
    expected_redirect_url = None
    if opportunity.managed:
        assert justification == visit.justification
        expected_redirect_url = reverse(
            "opportunity:user_visits_list",
            kwargs={"org_slug": opportunity.organization.slug, "opp_id": opportunity.id},
        )
    else:
        expected_redirect_url = reverse(
            "opportunity:user_visits_list",
            kwargs={
                "org_slug": opportunity.organization.slug,
                "opp_id": opportunity.id,
                "pk": visit.opportunity_access_id,
            },
        )
    assert response.redirect_chain[-1][0] == expected_redirect_url
    assert response.status_code == HTTPStatus.OK


@pytest.mark.django_db
def test_get_opportunity_list_data_all_annotations(opportunity):
    today = now().date()
    three_days_ago = now() - timedelta(days=3)

    opportunity.end_date = today + timedelta(days=1)
    opportunity.active = True
    opportunity.save()

    # Create OpportunityAccesses
    oa1 = OpportunityAccessFactory(opportunity=opportunity, accepted=True, payment_accrued=300)
    oa2 = OpportunityAccessFactory(opportunity=opportunity, accepted=True, payment_accrued=200)
    oa3 = OpportunityAccessFactory(opportunity=opportunity, accepted=True, payment_accrued=0)

    # Payments
    PaymentFactory(opportunity_access=oa1, amount_usd=100, confirmed=True)
    PaymentFactory(opportunity_access=oa2, amount_usd=50, confirmed=True)
    PaymentFactory(opportunity_access=oa1, amount_usd=999, confirmed=False)
    PaymentFactory(opportunity_access=oa3, amount_usd=0, confirmed=True)

    # Invites
    for _ in range(3):
        UserInviteFactory(opportunity=opportunity, status=UserInviteStatus.invited)
    UserInviteFactory(opportunity=opportunity, status=UserInviteStatus.accepted)

    # Visits
    UserVisitFactory(
        opportunity=opportunity, opportunity_access=oa1, status=VisitValidationStatus.pending, visit_date=now()
    )

    UserVisitFactory(
        opportunity=opportunity,
        opportunity_access=oa2,
        status=VisitValidationStatus.approved,
        visit_date=three_days_ago - timedelta(days=1),
    )

    UserVisitFactory(
        opportunity=opportunity,
        opportunity_access=oa3,
        status=VisitValidationStatus.rejected,
        visit_date=three_days_ago - timedelta(days=1),
    )

    queryset = get_opportunity_list_data(opportunity.organization)
    opp = queryset[0]

    assert opp.pending_invites == 3
    assert opp.pending_approvals == 1
    assert opp.total_accrued == Decimal("500")
    assert opp.total_paid == Decimal("150")
    assert opp.payments_due == Decimal("350")
    assert opp.inactive_workers == 2
    assert opp.status == 0
