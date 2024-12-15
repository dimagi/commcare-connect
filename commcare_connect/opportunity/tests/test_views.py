from unittest.mock import MagicMock, patch

import pytest
from django.test import Client
from django.urls import reverse
from django.utils.timezone import now

from commcare_connect.opportunity.models import (
    Opportunity,
    OpportunityAccess,
    OpportunityClaimLimit,
    UserVisit,
    VisitReviewStatus,
    VisitValidationStatus,
)
from commcare_connect.opportunity.payment_number_report import update_payment_number_statuses
from commcare_connect.opportunity.tests.factories import (
    OpportunityAccessFactory,
    OpportunityClaimFactory,
    OpportunityClaimLimitFactory,
    PaymentUnitFactory,
    UserVisitFactory,
)
from commcare_connect.organization.models import Organization, OrgUserPaymentNumberStatus
from commcare_connect.program.tests.factories import ManagedOpportunityFactory, ProgramFactory
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


class TestUserVisitReviewView:
    @pytest.fixture(autouse=True)
    def setup(
        self,
        client: Client,
        program_manager_org: Organization,
        program_manager_org_user_admin: User,
        organization: Organization,
        org_user_admin: User,
    ):
        self.client = client
        self.pm_org = program_manager_org
        self.pm_user = program_manager_org_user_admin
        self.nm_org = organization
        self.nm_user = org_user_admin
        self.program = ProgramFactory(organization=self.pm_org)
        self.opportunity = ManagedOpportunityFactory(program=self.program, organization=self.nm_org)
        access = OpportunityAccessFactory(opportunity=self.opportunity, accepted=True)
        self.visits = UserVisitFactory.create_batch(
            10,
            opportunity=self.opportunity,
            status=VisitValidationStatus.approved,
            review_created_on=now(),
            review_status=VisitReviewStatus.pending,
            opportunity_access=access,
        )

    def test_user_visit_review_program_manager_table(self):
        self.url = reverse("opportunity:user_visit_review", args=(self.pm_org.slug, self.opportunity.id))
        self.client.force_login(self.pm_user)
        response = self.client.get(self.url)
        assert response.status_code == 200
        table = response.context["table"]
        assert len(table.rows) == 10
        assert "pk" in table.columns.names()

    @pytest.mark.parametrize("review_status", [(VisitReviewStatus.agree), (VisitReviewStatus.disagree)])
    def test_user_visit_review_program_manager_approval(self, review_status):
        self.url = reverse("opportunity:user_visit_review", args=(self.pm_org.slug, self.opportunity.id))
        self.client.force_login(self.pm_user)
        response = self.client.post(self.url, {"pk": [], "review_status": review_status.value})
        assert response.status_code == 200
        visits = UserVisit.objects.filter(id__in=[visit.id for visit in self.visits])
        for visit in visits:
            assert visit.review_status == VisitReviewStatus.pending

        visit_ids = [visit.id for visit in self.visits][:5]
        response = self.client.post(self.url, {"pk": visit_ids, "review_status": review_status.value})
        assert response.status_code == 200
        visits = UserVisit.objects.filter(id__in=visit_ids)
        for visit in visits:
            assert visit.review_status == review_status

    def test_user_visit_review_network_manager_table(self):
        self.url = reverse("opportunity:user_visit_review", args=(self.nm_org.slug, self.opportunity.id))
        self.client.force_login(self.nm_user)
        response = self.client.get(self.url)
        table = response.context["table"]
        assert len(table.rows) == 10
        assert "pk" not in table.columns.names()
