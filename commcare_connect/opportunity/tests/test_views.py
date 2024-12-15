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
from commcare_connect.users.tests.factories import MobileUserFactory, OrganizationFactory


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
    "scenario",
    [
        {
            "name": "new_entries",
            "existing_statuses": [],
            "update_data": [
                {
                    "username": "test_user1",
                    "phone_number": "+1234567890",
                    "status": OrgUserPaymentNumberStatus.APPROVED,
                },
                {
                    "username": "test_user2",
                    "phone_number": "+9876543210",
                    "status": OrgUserPaymentNumberStatus.REJECTED,
                },
            ],
            "expected_result": {"approved": 1, "rejected": 1, "pending": 0},
            "expect_connectid_update": True,
            "expect_message": False,
        },
        {
            "name": "new_entries_partial",
            "existing_statuses": [
                {
                    "username": "test_user1",
                    "phone_number": "+1234567890",
                    "status": OrgUserPaymentNumberStatus.REJECTED,
                }
            ],
            "update_data": [
                {
                    "username": "test_user1",
                    "phone_number": "+1234567890",
                    "status": OrgUserPaymentNumberStatus.APPROVED,
                },
                {
                    "username": "test_user2",
                    "phone_number": "+9876543210",
                    "status": OrgUserPaymentNumberStatus.REJECTED,
                },
            ],
            "expected_result": {"approved": 1, "rejected": 1, "pending": 0},
            "expect_connectid_update": True,
            "expect_message": False,
        },
        {
            "name": "unchanged_status",
            "existing_statuses": [
                {
                    "username": "test_user1",
                    "phone_number": "+1234567890",
                    "status": OrgUserPaymentNumberStatus.APPROVED,
                }
            ],
            "update_data": [
                {
                    "username": "test_user1",
                    "phone_number": "+1234567890",
                    "status": OrgUserPaymentNumberStatus.APPROVED,
                }
            ],
            "expected_result": {"approved": 0, "rejected": 0, "pending": 0},
            "expect_connectid_update": False,
            "expect_message": False,
        },
        {
            "name": "changed_status",
            "existing_statuses": [
                {
                    "username": "test_user1",
                    "phone_number": "+1234567890",
                    "status": OrgUserPaymentNumberStatus.APPROVED,
                }
            ],
            "update_data": [
                {"username": "test_user1", "phone_number": "+1234567890", "status": OrgUserPaymentNumberStatus.PENDING}
            ],
            "expected_result": {"approved": 0, "rejected": 0, "pending": 1},
            "expect_connectid_update": True,
            "expect_message": False,
        },
        {
            "name": "phone_changed",
            "existing_statuses": [
                {
                    "username": "test_user1",
                    "phone_number": "+1234567890",
                    "status": OrgUserPaymentNumberStatus.APPROVED,
                }
            ],
            "update_data": [
                {
                    "username": "test_user1",
                    "phone_number": "+2344567890",
                    "status": OrgUserPaymentNumberStatus.APPROVED,
                }
            ],
            "expected_result": {"approved": 1, "rejected": 0, "pending": 0},
            "expect_connectid_update": True,
            "expect_message": False,
        },
        {
            "name": "inter_org_conflict",
            "existing_statuses": [
                {
                    "username": "test_user1",
                    "phone_number": "+1234567890",
                    "status": OrgUserPaymentNumberStatus.APPROVED,
                    "organization": "another_org",
                }
            ],
            "update_data": [
                {
                    "username": "test_user1",
                    "phone_number": "+1234567890",
                    "status": OrgUserPaymentNumberStatus.REJECTED,
                }
            ],
            "expected_result": {"approved": 0, "rejected": 1, "pending": 0},
            "expect_connectid_update": False,
            "expect_message": True,
        },
    ],
)
def test_update_payment_number_statuses(scenario, opportunity):
    # Prepare existing statuses
    usernames = {s["username"] for s in scenario["existing_statuses"] + scenario["update_data"]}
    by_username = {}
    for username in usernames:
        by_username[username] = MobileUserFactory(username=username)

    for status_data in scenario.get("existing_statuses", []):
        org = (
            opportunity.organization
            if status_data.get("organization") != "another_org"
            else OrganizationFactory(name=status_data["organization"])
        )
        user = by_username[status_data["username"]]

        OrgUserPaymentNumberStatus.objects.create(
            organization=org, user=user, phone_number=status_data["phone_number"], status=status_data["status"]
        )

    # Mock external service calls
    with patch(
        "commcare_connect.opportunity.payment_number_report.update_payment_statuses"
    ) as mock_update_connectid, patch(
        "commcare_connect.opportunity.payment_number_report.send_message_bulk"
    ) as mock_send_message:
        mock_update_connectid.return_value = MagicMock(status=200)
        result = update_payment_number_statuses(scenario["update_data"], opportunity)

        if scenario["expect_connectid_update"]:
            mock_update_connectid.assert_called_once()
        else:
            mock_update_connectid.assert_not_called()

        if scenario["expect_message"]:
            mock_send_message.assert_called_once()
        else:
            mock_send_message.assert_not_called()

        assert result == scenario["expected_result"]

        # Verify database entries
        for entry in scenario["update_data"]:
            try:
                status_obj = OrgUserPaymentNumberStatus.objects.get(
                    user__username=entry["username"],
                    organization=opportunity.organization,
                )
                assert status_obj.phone_number == entry["phone_number"]
                assert status_obj.status == entry["status"]
            except OrgUserPaymentNumberStatus.DoesNotExist:
                if scenario["expected_result"]["approved"] + scenario["expected_result"]["rejected"] > 0:
                    pytest.fail(f"Expected status entry not found for {entry}")
