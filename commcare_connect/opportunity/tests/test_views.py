from datetime import timedelta
from http import HTTPStatus

import pytest
from django.test import Client
from django.urls import reverse
from django.utils.timezone import now

from commcare_connect.opportunity.helpers import OpportunityData, TieredQueryset
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
    OpportunityFactory,
    PaymentFactory,
    PaymentUnitFactory,
    UserInviteFactory,
    UserVisitFactory,
)
from commcare_connect.opportunity.views import WorkerPaymentsView
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
    approve_url = reverse("opportunity:approve_visits", args=(opportunity.organization.slug, opportunity.id))
    client.force_login(user)
    response = client.post(approve_url, {"justification": justification, "visit_ids[]": [visit.id]}, follow=True)
    visit.refresh_from_db()
    assert visit.status == VisitValidationStatus.approved
    if opportunity.managed:
        assert justification == visit.justification
    assert response.status_code == HTTPStatus.OK


@pytest.mark.django_db
@pytest.mark.parametrize(
    "filters, expected_count",
    [
        ({}, 4),
        ({"is_test": True}, 1),
        ({"is_test": False}, 3),
        ({"status": [0]}, 2),
        ({"status": [1]}, 1),
        ({"status": [2]}, 1),
        ({"program": ["test-program-1"]}, 1),
        ({"program": ["test-program-2"]}, 1),
        ({"program": ["test-program-1", "test-program-2"]}, 2),
    ],
)
def test_get_opportunity_list_data_all_annotations(organization, filters, expected_count):
    today = now().date()
    three_days_ago = now() - timedelta(days=3)

    program1 = ProgramFactory(organization=organization, name="Test Program 1", slug="test-program-1")
    program2 = ProgramFactory(organization=organization, name="Test Program 2", slug="test-program-2")

    # Active opportunity (status=0)
    opportunity = ManagedOpportunityFactory(
        program=program1,
        organization=organization,
        end_date=today + timedelta(days=1),
        active=True,
        is_test=True,
    )

    # Active opportunity (status=0)
    ManagedOpportunityFactory(
        program=program2,
        organization=organization,
        name="test opportunity 2",
        end_date=today + timedelta(days=1),
        active=True,
        is_test=False,
    )

    # Ended opportunity (status=1)
    OpportunityFactory(
        organization=organization,
        name="test opportunity 3",
        end_date=today - timedelta(days=1),
        active=True,
        is_test=False,
    )

    # Inactive opportunity (status=2)
    OpportunityFactory(
        organization=organization,
        name="test opportunity 4",
        end_date=today + timedelta(days=1),
        active=False,
        is_test=False,
    )

    # Create OpportunityAccesses
    oa1 = OpportunityAccessFactory(opportunity=opportunity, accepted=True, payment_accrued=1000, last_active=now())
    oa2 = OpportunityAccessFactory(
        opportunity=opportunity, accepted=True, payment_accrued=200, last_active=now() - timedelta(4)
    )
    oa3 = OpportunityAccessFactory(
        opportunity=opportunity, accepted=True, payment_accrued=0, last_active=now() - timedelta(4)
    )

    # Payments
    PaymentFactory(opportunity_access=oa1, amount=100, confirmed=True)
    PaymentFactory(opportunity_access=oa2, amount=50, confirmed=True)
    PaymentFactory(opportunity_access=oa1, amount=999, confirmed=False)
    PaymentFactory(opportunity_access=oa3, amount=0, confirmed=True)

    total_paid = 1149
    total_accrued = 1200

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
        visit_date=three_days_ago - timedelta(days=2),
    )

    UserVisitFactory(
        opportunity=opportunity,
        opportunity_access=oa3,
        status=VisitValidationStatus.rejected,
        visit_date=three_days_ago - timedelta(days=2),
    )

    queryset = OpportunityData(organization, False, filters).get_data()
    assert queryset.count() == expected_count
    if not filters:
        opp = queryset[0]
        assert opp.pending_invites == 3
        assert opp.pending_approvals == 1
        assert opp.total_accrued == total_accrued
        assert opp.total_paid == total_paid
        assert opp.payments_due == total_accrued - total_paid
        assert opp.inactive_workers == 2
        assert opp.status == 0


@pytest.mark.django_db
def test_tiered_queryset_basic():
    users = [User.objects.create(username=f"user{i}") for i in range(5)]
    base_qs = User.objects.all()

    def data_qs_fn(ids):
        qs = User.objects.filter(id__in=ids)
        return sorted(qs, key=lambda u: ids.index(u.id))

    tq = TieredQueryset(base_qs, data_qs_fn)

    assert tq.count() == 5

    # Single item access
    first_user = tq[0]
    assert first_user.username == users[0].username

    # Slice access
    sliced = tq[1:3]
    assert [u.username for u in sliced] == [users[1].username, users[2].username]

    # Iteration returns all
    all_users = list(tq)
    assert [u.username for u in all_users] == [u.username for u in users]

    # Order_by works
    tq.order_by("-id")
    desc_users = list(tq[:2])
    expected = list(User.objects.order_by("-id")[:2])
    assert [u.id for u in desc_users] == [u.id for u in expected]

    # Empty slice works
    assert tq[100:105] == []


@pytest.mark.django_db
@pytest.mark.parametrize(
    "referring_url, should_persist",
    [
        ("deliver_tab", True),
        ("/somewhere-else", False),
    ],
)
def test_tab_param_persistence(rf, opportunity, organization, referring_url, should_persist):
    tab_a_url = reverse("opportunity:worker_deliver", args=(organization.slug, opportunity.id))
    tab_b_url = reverse("opportunity:worker_payments", args=(organization.slug, opportunity.id))

    # Step 1: Visit tab A with GET params from any non-tab page
    request_a = rf.get(tab_a_url, {"status": "active"}, HTTP_REFERER="/anywhere-else")
    request_a.session = {}
    view = WorkerPaymentsView()
    view.request = request_a
    _ = view.get_tabs(organization.slug, opportunity)
    assert "worker_tab_params:payments" in request_a.session

    # Step 2: Go to tab B, with referrer varying
    if referring_url == "deliver_tab":
        referrer = tab_a_url
    else:
        referrer = referring_url

    request_b = rf.get(tab_b_url, HTTP_REFERER=referrer)
    request_b.session = request_a.session
    view.request = request_b
    tabs_b = view.get_tabs(organization.slug, opportunity)

    tab_a_link = [t["url"] for t in tabs_b if t["key"] == "payments"][0]

    if should_persist:
        assert "status=active" in tab_a_link
    else:
        assert "status=active" not in tab_a_link
