from unittest.mock import MagicMock, patch

import pytest
from django.test import Client
from django.urls import reverse

from commcare_connect.opportunity.models import Opportunity, OpportunityAccess, OpportunityClaimLimit
from commcare_connect.opportunity.payment_number_report import update_payment_number_statuses
from commcare_connect.opportunity.tests.factories import (
    OpportunityClaimFactory,
    OpportunityClaimLimitFactory,
    PaymentUnitFactory,
)
from commcare_connect.organization.models import Organization, OrgUserPaymentNumberStatus
from commcare_connect.users.models import User


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


@pytest.mark.django_db
@pytest.mark.parametrize(
    "update_data, existing_statuses, expected_updates",
    [
        # Case 1: Agreement exists, an org disagrees
        (
            [{"username": "user1", "phone_number": "123", "status": "REJECTED"}],
            [OrgUserPaymentNumberStatus(user=User(username="user1"), phone_number="123", status="APPROVED")],
            [{"username": "user1", "status": "REJECTED"}],
        ),
        # Case 2: Disagreement turns into agreement
        (
            [{"username": "user2", "phone_number": "456", "status": "APPROVED"}],
            [OrgUserPaymentNumberStatus(user=User(username="user2"), phone_number="456", status="REJECTED")],
            [{"username": "user2", "status": "APPROVED"}],
        ),
        # Case 3: No other org has any data
        (
            [{"username": "user3", "phone_number": "789", "status": "APPROVED"}],
            [],
            [{"username": "user3", "status": "APPROVED"}],
        ),
        # Case 4: No changes
        (
            [{"username": "user4", "phone_number": "101", "status": "APPROVED"}],
            [OrgUserPaymentNumberStatus(user=User(username="user4"), phone_number="101", status="APPROVED")],
            [],
        ),
        # Case 5: Pending status update
        (
            [{"username": "user5", "phone_number": "112", "status": "PENDING"}],
            [OrgUserPaymentNumberStatus(user=User(username="user5"), phone_number="112", status="REJECTED")],
            [{"username": "user5", "status": "PENDING"}],
        ),
    ],
)
@patch("commcare_connect.opportunity.payment_number_report.send_message_bulk")
@patch("commcare_connect.opportunity.payment_number_report.update_payment_statuses")
@patch("commcare_connect.organization.models.OrgUserPaymentNumberStatus.objects.filter")
@patch("commcare_connect.users.models.User.objects.filter")
def test_validate_payment_profiles(
    mock_user_filter,
    mock_status_filter,
    mock_update_payment_statuses,
    mock_send_message_bulk,
    update_data,
    existing_statuses,
    expected_updates,
):
    # Setup mocks
    mock_users = [User(username=u["username"]) for u in update_data]
    mock_user_filter.return_value = MagicMock(all=MagicMock(return_value=mock_users))

    mock_update_payment_statuses.return_value = MagicMock(status=200, json=lambda: {"result": "success"})

    organization = Organization(name="Test Organization")

    opportunity = MagicMock(name="Test Opportunity", organization=organization)

    # Call the function
    update_payment_number_statuses(update_data, opportunity)

    # Assertions
    if expected_updates:
        mock_update_payment_statuses.assert_called_once_with(expected_updates)
    else:
        mock_update_payment_statuses.assert_not_called()
