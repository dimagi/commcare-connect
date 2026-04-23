import datetime
from unittest.mock import patch

import pytest

from commcare_connect.commcarehq.tests.factories import HQServerFactory
from commcare_connect.opportunity.models import PaymentUnit
from commcare_connect.opportunity.tests.factories import (
    CommCareAppFactory,
    DeliverUnitFactory,
    DeliveryTypeFactory,
    HQApiKeyFactory,
    PaymentUnitFactory,
)
from commcare_connect.program.tests.factories import ManagedOpportunityFactory, ProgramFactory


@pytest.fixture
def managed_opp_with_deliver_units(program_manager_org, organization):
    """A managed opportunity with two deliver units, ready for payment unit tests."""
    program = ProgramFactory(organization=program_manager_org)
    hq_server = HQServerFactory()
    learn_app = CommCareAppFactory(organization=organization, hq_server=hq_server)
    deliver_app = CommCareAppFactory(organization=organization, hq_server=hq_server)
    opportunity = ManagedOpportunityFactory(
        program=program,
        organization=organization,
        learn_app=learn_app,
        deliver_app=deliver_app,
        active=False,
        hq_server=hq_server,
    )
    du1 = DeliverUnitFactory(app=deliver_app, slug="du-1", name="DU 1", payment_unit=None)
    du2 = DeliverUnitFactory(app=deliver_app, slug="du-2", name="DU 2", payment_unit=None)
    return opportunity, du1, du2


@pytest.fixture
def active_managed_opportunity(program_manager_org, organization):
    program = ProgramFactory(organization=program_manager_org)
    return ManagedOpportunityFactory(program=program, organization=organization, active=True)


@pytest.mark.django_db
class TestPaymentUnits:
    def test_create_payment_units(self, api_client, program_manager_org_user_admin, managed_opp_with_deliver_units):
        opportunity, du1, du2 = managed_opp_with_deliver_units
        api_client.force_authenticate(program_manager_org_user_admin)
        response = api_client.post(
            f"/api/opportunities/{opportunity.opportunity_id}/payment_units/",
            {
                "payment_units": [
                    {
                        "name": "Visit",
                        "description": "A visit",
                        "amount": 500,
                        "org_amount": 100,
                        "max_total": 50,
                        "max_daily": 10,
                        "required_deliver_units": [du1.id],
                        "optional_deliver_units": [du2.id],
                    }
                ]
            },
            format="json",
        )
        assert response.status_code == 201
        assert len(response.data["payment_units"]) == 1
        pu = PaymentUnit.objects.get(opportunity=opportunity)
        assert pu.name == "Visit"
        assert pu.amount == 500
        assert pu.org_amount == 100
        deliver_units = pu.deliver_units.all()
        required = [d for d in deliver_units if not d.optional]
        optional = [d for d in deliver_units if d.optional]
        assert len(required) == 1
        assert len(optional) == 1

    def test_payment_unit_invalid_deliver_unit(
        self, api_client, program_manager_org_user_admin, managed_opp_with_deliver_units
    ):
        opportunity, du1, du2 = managed_opp_with_deliver_units
        api_client.force_authenticate(program_manager_org_user_admin)
        response = api_client.post(
            f"/api/opportunities/{opportunity.opportunity_id}/payment_units/",
            {
                "payment_units": [
                    {
                        "name": "Visit",
                        "description": "A visit",
                        "amount": 500,
                        "org_amount": 100,
                        "max_total": 50,
                        "max_daily": 10,
                        "required_deliver_units": [99999],
                        "optional_deliver_units": [],
                    }
                ]
            },
            format="json",
        )
        assert response.status_code == 400

    def test_payment_unit_missing_org_amount_for_managed(
        self, api_client, program_manager_org_user_admin, managed_opp_with_deliver_units
    ):
        opportunity, du1, du2 = managed_opp_with_deliver_units
        api_client.force_authenticate(program_manager_org_user_admin)
        response = api_client.post(
            f"/api/opportunities/{opportunity.opportunity_id}/payment_units/",
            {
                "payment_units": [
                    {
                        "name": "Visit",
                        "description": "A visit",
                        "amount": 500,
                        "max_total": 50,
                        "max_daily": 10,
                        "required_deliver_units": [du1.id],
                        "optional_deliver_units": [],
                    }
                ]
            },
            format="json",
        )
        assert response.status_code == 400

    def test_payment_unit_rejects_overlap_between_required_and_optional(
        self, api_client, program_manager_org_user_admin, managed_opp_with_deliver_units
    ):
        opportunity, du1, du2 = managed_opp_with_deliver_units
        api_client.force_authenticate(program_manager_org_user_admin)
        response = api_client.post(
            f"/api/opportunities/{opportunity.opportunity_id}/payment_units/",
            {
                "payment_units": [
                    {
                        "name": "Visit",
                        "description": "A visit",
                        "amount": 500,
                        "org_amount": 100,
                        "max_total": 50,
                        "max_daily": 10,
                        "required_deliver_units": [du1.id],
                        "optional_deliver_units": [du1.id],
                    }
                ]
            },
            format="json",
        )
        assert response.status_code == 400

    def test_payment_unit_rejects_already_assigned_deliver_unit(
        self, api_client, program_manager_org_user_admin, managed_opp_with_deliver_units
    ):
        opportunity, du1, du2 = managed_opp_with_deliver_units
        # Pre-assign du1 to another PaymentUnit
        existing_pu = PaymentUnitFactory(opportunity=opportunity, amount=1, org_amount=1, max_total=1)
        du1.payment_unit = existing_pu
        du1.save()

        api_client.force_authenticate(program_manager_org_user_admin)
        response = api_client.post(
            f"/api/opportunities/{opportunity.opportunity_id}/payment_units/",
            {
                "payment_units": [
                    {
                        "name": "Visit",
                        "description": "A visit",
                        "amount": 500,
                        "org_amount": 100,
                        "max_total": 50,
                        "max_daily": 10,
                        "required_deliver_units": [du1.id],
                        "optional_deliver_units": [],
                    }
                ]
            },
            format="json",
        )
        assert response.status_code == 400

    def test_payment_unit_rejects_same_du_across_multiple_payment_units_in_request(
        self, api_client, program_manager_org_user_admin, managed_opp_with_deliver_units
    ):
        opportunity, du1, du2 = managed_opp_with_deliver_units
        api_client.force_authenticate(program_manager_org_user_admin)
        response = api_client.post(
            f"/api/opportunities/{opportunity.opportunity_id}/payment_units/",
            {
                "payment_units": [
                    {
                        "name": "Visit A",
                        "description": "",
                        "amount": 500,
                        "org_amount": 100,
                        "max_total": 50,
                        "max_daily": 10,
                        "required_deliver_units": [du1.id],
                        "optional_deliver_units": [],
                    },
                    {
                        "name": "Visit B",
                        "description": "",
                        "amount": 500,
                        "org_amount": 100,
                        "max_total": 50,
                        "max_daily": 10,
                        "required_deliver_units": [du1.id],  # conflict
                        "optional_deliver_units": [],
                    },
                ]
            },
            format="json",
        )
        assert response.status_code == 400

    def test_payment_units_cross_tenant_rejected(self, api_client, managed_opp_with_deliver_units):
        """Admin of a different program manager org cannot add payment units to someone else's opportunity."""
        opportunity, du1, du2 = managed_opp_with_deliver_units
        # Create a separate program manager org — the user there has no relation to opportunity
        from commcare_connect.users.tests.factories import ProgramManagerOrgWithUsersFactory

        other_pm_org = ProgramManagerOrgWithUsersFactory()
        other_admin = other_pm_org.memberships.filter(role="admin").first().user
        api_client.force_authenticate(other_admin)
        response = api_client.post(
            f"/api/opportunities/{opportunity.opportunity_id}/payment_units/",
            {
                "payment_units": [
                    {
                        "name": "Visit",
                        "description": "",
                        "amount": 500,
                        "org_amount": 100,
                        "max_total": 50,
                        "max_daily": 10,
                        "required_deliver_units": [du1.id],
                        "optional_deliver_units": [],
                    }
                ]
            },
            format="json",
        )
        assert response.status_code == 403


@pytest.mark.django_db
class TestActivateOpportunity:
    def test_activate_success(self, api_client, program_manager_org_user_admin, managed_opp_with_deliver_units):
        opportunity, du1, du2 = managed_opp_with_deliver_units
        PaymentUnitFactory(opportunity=opportunity, amount=100, org_amount=10, max_total=50)
        api_client.force_authenticate(program_manager_org_user_admin)
        response = api_client.post(
            f"/api/opportunities/{opportunity.opportunity_id}/activate/",
        )
        assert response.status_code == 200
        opportunity.refresh_from_db()
        assert opportunity.active is True

    def test_activate_no_payment_units(
        self, api_client, program_manager_org_user_admin, program_manager_org, organization
    ):
        program = ProgramFactory(organization=program_manager_org)
        opportunity = ManagedOpportunityFactory(
            program=program,
            organization=organization,
            active=False,
        )
        # No payment units created
        api_client.force_authenticate(program_manager_org_user_admin)
        response = api_client.post(
            f"/api/opportunities/{opportunity.opportunity_id}/activate/",
        )
        assert response.status_code == 400

    def test_activate_already_active(self, api_client, program_manager_org_user_admin, active_managed_opportunity):
        api_client.force_authenticate(program_manager_org_user_admin)
        response = api_client.post(
            f"/api/opportunities/{active_managed_opportunity.opportunity_id}/activate/",
        )
        assert response.status_code == 400

    def test_activate_ended_opportunity_rejected(
        self, api_client, program_manager_org_user_admin, managed_opp_with_deliver_units
    ):
        opportunity, du1, du2 = managed_opp_with_deliver_units
        PaymentUnitFactory(opportunity=opportunity, amount=100, org_amount=10, max_total=50)
        opportunity.end_date = datetime.date.today() - datetime.timedelta(days=1)
        opportunity.save(update_fields=["end_date"])
        api_client.force_authenticate(program_manager_org_user_admin)
        response = api_client.post(
            f"/api/opportunities/{opportunity.opportunity_id}/activate/",
        )
        assert response.status_code == 400


@pytest.mark.django_db
class TestInviteUsers:
    @patch("commcare_connect.opportunity.api.views.automation.add_connect_users")
    def test_invite_users_success(
        self, mock_add_users, api_client, program_manager_org_user_admin, active_managed_opportunity
    ):
        api_client.force_authenticate(program_manager_org_user_admin)
        response = api_client.post(
            f"/api/opportunities/{active_managed_opportunity.opportunity_id}/invite_users/",
            {"phone_numbers": ["+265999111222", "+265999333444"]},
            format="json",
        )
        assert response.status_code == 202
        assert response.data["invited_count"] == 2
        mock_add_users.delay.assert_called_once_with(["+265999111222", "+265999333444"], active_managed_opportunity.id)

    def test_invite_users_inactive_opportunity(self, api_client, program_manager_org_user_admin, managed_opportunity):
        managed_opportunity.active = False
        managed_opportunity.save(update_fields=["active"])
        api_client.force_authenticate(program_manager_org_user_admin)
        response = api_client.post(
            f"/api/opportunities/{managed_opportunity.opportunity_id}/invite_users/",
            {"phone_numbers": ["+265999111222"]},
            format="json",
        )
        assert response.status_code == 400

    def test_invite_users_empty_list(self, api_client, program_manager_org_user_admin, active_managed_opportunity):
        api_client.force_authenticate(program_manager_org_user_admin)
        response = api_client.post(
            f"/api/opportunities/{active_managed_opportunity.opportunity_id}/invite_users/",
            {"phone_numbers": []},
            format="json",
        )
        assert response.status_code == 400


@pytest.mark.django_db
class TestFullPipeline:
    @patch("commcare_connect.program.api.serializers.get_applications_for_user_by_domain")
    @patch("commcare_connect.opportunity.api.views.automation.add_connect_users")
    @patch("commcare_connect.opportunity.tasks.get_connect_blocks_for_app")
    @patch("commcare_connect.opportunity.tasks.get_deliver_units_for_app")
    def test_full_automation_flow(
        self,
        mock_deliver_units,
        mock_learn_modules,
        mock_add_users,
        mock_hq_apps,
        api_client,
        program_manager_org,
        program_manager_org_user_admin,
        organization,
    ):
        mock_hq_apps.return_value = [
            {"id": "learn-e2e", "name": "E2E Learn App"},
            {"id": "deliver-e2e", "name": "E2E Deliver App"},
        ]
        from commcare_connect.opportunity.app_xml import DeliverUnit as DeliverUnitData
        from commcare_connect.opportunity.app_xml import Module

        mock_learn_modules.return_value = [Module(id="mod-1", name="Module 1", description="Desc", time_estimate=10)]
        mock_deliver_units.return_value = [
            DeliverUnitData(id="du-1", name="Deliver Unit 1"),
        ]

        delivery_type = DeliveryTypeFactory(slug="test-delivery")
        hq_server = HQServerFactory()
        hq_api_key = HQApiKeyFactory(user=program_manager_org_user_admin, hq_server=hq_server)
        api_client.force_authenticate(program_manager_org_user_admin)

        # Step 1: Create program
        response = api_client.post(
            "/api/programs/",
            {
                "name": "E2E Program",
                "description": "End to end test",
                "organization": program_manager_org.slug,
                "delivery_type": delivery_type.slug,
                "budget": 500000,
                "currency": "USD",
                "country": "United States of America",
                "start_date": "2026-05-01",
                "end_date": "2026-12-31",
            },
            format="json",
        )
        assert response.status_code == 201
        program_id = response.data["program_id"]

        # Step 2: Invite org
        response = api_client.post(
            f"/api/programs/{program_id}/applications/",
            {"organization": organization.slug},
            format="json",
        )
        assert response.status_code == 201
        application_id = response.data["program_application_id"]

        # Step 3: Accept org
        response = api_client.post(f"/api/programs/{program_id}/applications/{application_id}/accept/")
        assert response.status_code == 200

        # Step 4: Create opportunity
        response = api_client.post(
            f"/api/programs/{program_id}/opportunities/",
            {
                "name": "E2E Opportunity",
                "description": "Test opportunity",
                "short_description": "Short",
                "organization": organization.slug,
                "start_date": "2026-05-01",
                "end_date": "2026-12-31",
                "total_budget": 100000,
                "learn_app": {
                    "hq_server_url": hq_server.url,
                    "api_key": hq_api_key.api_key,
                    "cc_domain": "e2e-domain",
                    "cc_app_id": "learn-e2e",
                    "description": "Learn desc",
                    "passing_score": 75,
                },
                "deliver_app": {
                    "hq_server_url": hq_server.url,
                    "api_key": hq_api_key.api_key,
                    "cc_domain": "e2e-domain",
                    "cc_app_id": "deliver-e2e",
                },
            },
            format="json",
        )
        assert response.status_code == 201
        opportunity_id = response.data["opportunity_id"]
        deliver_unit_id = response.data["deliver_app"]["deliver_units"][0]["id"]

        # Step 5: Add payment units
        response = api_client.post(
            f"/api/opportunities/{opportunity_id}/payment_units/",
            {
                "payment_units": [
                    {
                        "name": "Visit",
                        "description": "A visit",
                        "amount": 500,
                        "org_amount": 100,
                        "max_total": 50,
                        "max_daily": 10,
                        "required_deliver_units": [deliver_unit_id],
                        "optional_deliver_units": [],
                    }
                ]
            },
            format="json",
        )
        assert response.status_code == 201

        # Step 6: Activate
        response = api_client.post(
            f"/api/opportunities/{opportunity_id}/activate/",
        )
        assert response.status_code == 200
        assert response.data["active"] is True

        # Step 7: Invite users
        response = api_client.post(
            f"/api/opportunities/{opportunity_id}/invite_users/",
            {"phone_numbers": ["+265999111222"]},
            format="json",
        )
        assert response.status_code == 202
        mock_add_users.delay.assert_called_once()
