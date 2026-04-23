import datetime
from unittest.mock import patch

import httpx
import pytest

from commcare_connect.commcarehq.tests.factories import HQServerFactory
from commcare_connect.opportunity.app_xml import DeliverUnit as DeliverUnitData
from commcare_connect.opportunity.app_xml import Module
from commcare_connect.opportunity.models import CommCareApp, HQApiKey
from commcare_connect.opportunity.tests.factories import DeliveryTypeFactory, HQApiKeyFactory
from commcare_connect.program.models import ManagedOpportunity, Program, ProgramApplication, ProgramApplicationStatus
from commcare_connect.program.tests.factories import ProgramApplicationFactory, ProgramFactory


@pytest.fixture
def delivery_type(db):
    return DeliveryTypeFactory(slug="test-delivery")


@pytest.fixture
def hq_server(db):
    return HQServerFactory()


@pytest.fixture
def hq_api_key(program_manager_org_user_admin, hq_server):
    return HQApiKeyFactory(user=program_manager_org_user_admin, hq_server=hq_server)


@pytest.fixture
def program(program_manager_org, delivery_type):
    return ProgramFactory(
        organization=program_manager_org,
        start_date=datetime.date(2026, 1, 1),
        end_date=datetime.date(2026, 12, 31),
        budget=500000,
    )


@pytest.fixture
def accepted_application(program, organization):
    return ProgramApplicationFactory(
        program=program,
        organization=organization,
        status=ProgramApplicationStatus.ACCEPTED,
    )


@pytest.fixture
def mock_hq_apps(monkeypatch):
    """Mock get_applications_for_user_by_domain to return both test app IDs."""

    def fake_get_apps(api_key, domain):
        return [
            {"id": "learn-app-123", "name": "Test Learn App"},
            {"id": "deliver-app-456", "name": "Test Deliver App"},
            {"id": "learn-e2e", "name": "E2E Learn App"},
            {"id": "deliver-e2e", "name": "E2E Deliver App"},
        ]

    monkeypatch.setattr(
        "commcare_connect.program.api.serializers.get_applications_for_user_by_domain",
        fake_get_apps,
    )


@pytest.mark.django_db
class TestPermissions:
    def test_unauthenticated_user_rejected(self, api_client):
        response = api_client.post("/api/programs/", {})
        assert response.status_code == 401

    def test_non_admin_user_rejected(self, api_client, program_manager_org, program_manager_org_user_member):
        api_client.force_authenticate(program_manager_org_user_member)
        response = api_client.post("/api/programs/", {"organization": program_manager_org.slug}, format="json")
        assert response.status_code == 403

    def test_non_program_manager_org_rejected(self, api_client, organization, org_user_admin, delivery_type):
        api_client.force_authenticate(org_user_admin)
        response = api_client.post(
            "/api/programs/",
            {
                "organization": organization.slug,
                "name": "Test",
                "description": "Test",
                "delivery_type": delivery_type.slug,
                "budget": 1000,
                "currency": "USD",
                "country": "United States of America",
                "start_date": "2026-05-01",
                "end_date": "2026-12-31",
            },
            format="json",
        )
        assert response.status_code == 403


def _program_payload(org, delivery_type):
    return {
        "name": "Test Program",
        "description": "A test program",
        "organization": org.slug,
        "delivery_type": delivery_type.slug,
        "budget": 500000,
        "currency": "USD",
        "country": "United States of America",
        "start_date": "2026-05-01",
        "end_date": "2026-12-31",
    }


@pytest.mark.django_db
class TestProgramCreate:
    def test_create_program_success(
        self, api_client, program_manager_org, program_manager_org_user_admin, delivery_type
    ):
        api_client.force_authenticate(program_manager_org_user_admin)
        response = api_client.post(
            "/api/programs/",
            _program_payload(program_manager_org, delivery_type),
            format="json",
        )
        assert response.status_code == 201
        assert response.data["name"] == "Test Program"
        assert response.data["program_id"] is not None
        assert response.data["organization"] == program_manager_org.slug
        assert Program.objects.filter(name="Test Program").exists()

    def test_create_program_end_before_start(
        self, api_client, program_manager_org, program_manager_org_user_admin, delivery_type
    ):
        api_client.force_authenticate(program_manager_org_user_admin)
        payload = _program_payload(program_manager_org, delivery_type)
        payload["start_date"] = "2026-12-31"
        payload["end_date"] = "2026-05-01"
        response = api_client.post("/api/programs/", payload, format="json")
        assert response.status_code == 400

    def test_create_program_nonexistent_org(self, api_client, user):
        api_client.force_authenticate(user)
        response = api_client.post(
            "/api/programs/",
            {
                "name": "Test",
                "description": "Test",
                "organization": "nonexistent",
                "delivery_type": "test",
                "budget": 1000,
                "currency": "USD",
                "country": "United States of America",
                "start_date": "2026-05-01",
                "end_date": "2026-12-31",
            },
            format="json",
        )
        assert response.status_code == 403


@pytest.mark.django_db
class TestProgramApplication:
    def test_invite_organization(self, api_client, program_manager_org_user_admin, program, organization):
        api_client.force_authenticate(program_manager_org_user_admin)
        response = api_client.post(
            f"/api/programs/{program.program_id}/applications/",
            {"organization": organization.slug},
            format="json",
        )
        assert response.status_code == 201
        assert response.data["status"] == "invited"
        assert response.data["organization"] == organization.slug
        assert ProgramApplication.objects.filter(program=program, organization=organization).exists()

    def test_accept_application(self, api_client, program_manager_org_user_admin, program):
        application = ProgramApplicationFactory(program=program, status=ProgramApplicationStatus.INVITED)
        api_client.force_authenticate(program_manager_org_user_admin)
        response = api_client.post(
            f"/api/programs/{program.program_id}/applications/{application.program_application_id}/accept/",
        )
        assert response.status_code == 200
        application.refresh_from_db()
        assert application.status == ProgramApplicationStatus.ACCEPTED

    def test_accept_already_accepted_fails(self, api_client, program_manager_org_user_admin, program):
        application = ProgramApplicationFactory(program=program, status=ProgramApplicationStatus.ACCEPTED)
        api_client.force_authenticate(program_manager_org_user_admin)
        response = api_client.post(
            f"/api/programs/{program.program_id}/applications/{application.program_application_id}/accept/",
        )
        assert response.status_code == 400

    def test_invite_requires_program_org_admin(self, api_client, user, program, organization):
        api_client.force_authenticate(user)
        response = api_client.post(
            f"/api/programs/{program.program_id}/applications/",
            {"organization": organization.slug},
            format="json",
        )
        assert response.status_code == 403


def _opportunity_payload(org, hq_server, hq_api_key):
    return {
        "name": "Test Opportunity",
        "description": "A test opportunity",
        "short_description": "Short desc",
        "organization": org.slug,
        "start_date": "2026-05-01",
        "end_date": "2026-12-31",
        "total_budget": 100000,
        "learn_app": {
            "hq_server_url": hq_server.url,
            "api_key": hq_api_key.api_key,
            "cc_domain": "test-domain",
            "cc_app_id": "learn-app-123",
            "description": "Learn app desc",
            "passing_score": 80,
        },
        "deliver_app": {
            "hq_server_url": hq_server.url,
            "api_key": hq_api_key.api_key,
            "cc_domain": "test-domain",
            "cc_app_id": "deliver-app-456",
        },
    }


@pytest.mark.django_db
class TestManagedOpportunityCreate:
    @patch("commcare_connect.opportunity.tasks.get_connect_blocks_for_app")
    @patch("commcare_connect.opportunity.tasks.get_deliver_units_for_app")
    def test_create_managed_opportunity(
        self,
        mock_deliver_units,
        mock_learn_modules,
        mock_hq_apps,
        api_client,
        program_manager_org_user_admin,
        program,
        organization,
        accepted_application,
        hq_server,
        hq_api_key,
    ):
        mock_learn_modules.return_value = [Module(id="mod-1", name="Module 1", description="Desc", time_estimate=10)]
        mock_deliver_units.return_value = [
            DeliverUnitData(id="du-1", name="Deliver Unit 1"),
            DeliverUnitData(id="du-2", name="Deliver Unit 2"),
        ]

        api_client.force_authenticate(program_manager_org_user_admin)
        response = api_client.post(
            f"/api/programs/{program.program_id}/opportunities/",
            _opportunity_payload(organization, hq_server, hq_api_key),
            format="json",
        )
        assert response.status_code == 201
        assert response.data["managed"] is True
        assert response.data["program_id"] == str(program.program_id)
        assert response.data["organization"] == organization.slug
        assert response.data["start_date"] == "2026-05-01"
        assert response.data["end_date"] == "2026-12-31"
        assert response.data["total_budget"] == 100000
        assert response.data["active"] is False
        assert len(response.data["learn_app"]["learn_modules"]) == 1
        assert len(response.data["deliver_app"]["deliver_units"]) == 2

        opp = ManagedOpportunity.objects.get(opportunity_id=response.data["opportunity_id"])
        assert opp.organization == organization
        assert opp.program == program
        assert opp.currency == program.currency
        assert opp.delivery_type == program.delivery_type
        assert opp.learn_app.name == "Test Learn App"
        assert opp.deliver_app.name == "Test Deliver App"

    @patch("commcare_connect.opportunity.tasks.get_connect_blocks_for_app")
    @patch("commcare_connect.opportunity.tasks.get_deliver_units_for_app")
    def test_create_opportunity_same_learn_deliver_app_fails(
        self,
        mock_deliver_units,
        mock_learn_modules,
        mock_hq_apps,
        api_client,
        program_manager_org_user_admin,
        program,
        organization,
        accepted_application,
        hq_server,
        hq_api_key,
    ):
        api_client.force_authenticate(program_manager_org_user_admin)
        payload = _opportunity_payload(organization, hq_server, hq_api_key)
        payload["deliver_app"]["cc_app_id"] = payload["learn_app"]["cc_app_id"]
        response = api_client.post(
            f"/api/programs/{program.program_id}/opportunities/",
            payload,
            format="json",
        )
        assert response.status_code == 400

    @patch("commcare_connect.opportunity.tasks.get_connect_blocks_for_app")
    @patch("commcare_connect.opportunity.tasks.get_deliver_units_for_app")
    def test_create_opportunity_unaccepted_org_fails(
        self,
        mock_deliver_units,
        mock_learn_modules,
        mock_hq_apps,
        api_client,
        program_manager_org_user_admin,
        program,
        organization,
        accepted_application,
        hq_server,
        hq_api_key,
    ):
        ProgramApplication.objects.filter(program=program).update(status=ProgramApplicationStatus.INVITED)
        api_client.force_authenticate(program_manager_org_user_admin)
        response = api_client.post(
            f"/api/programs/{program.program_id}/opportunities/",
            _opportunity_payload(organization, hq_server, hq_api_key),
            format="json",
        )
        assert response.status_code == 400

    @patch("commcare_connect.opportunity.tasks.get_connect_blocks_for_app")
    @patch("commcare_connect.opportunity.tasks.get_deliver_units_for_app")
    def test_create_opportunity_dates_outside_program_fails(
        self,
        mock_deliver_units,
        mock_learn_modules,
        mock_hq_apps,
        api_client,
        program_manager_org_user_admin,
        program,
        organization,
        accepted_application,
        hq_server,
        hq_api_key,
    ):
        api_client.force_authenticate(program_manager_org_user_admin)
        payload = _opportunity_payload(organization, hq_server, hq_api_key)
        payload["start_date"] = "2020-01-01"
        response = api_client.post(
            f"/api/programs/{program.program_id}/opportunities/",
            payload,
            format="json",
        )
        assert response.status_code == 400

    @patch("commcare_connect.opportunity.tasks.get_connect_blocks_for_app")
    @patch("commcare_connect.opportunity.tasks.get_deliver_units_for_app")
    def test_create_opportunity_budget_exceeds_program_fails(
        self,
        mock_deliver_units,
        mock_learn_modules,
        mock_hq_apps,
        api_client,
        program_manager_org_user_admin,
        program,
        organization,
        accepted_application,
        hq_server,
        hq_api_key,
    ):
        api_client.force_authenticate(program_manager_org_user_admin)
        payload = _opportunity_payload(organization, hq_server, hq_api_key)
        payload["total_budget"] = 999999999
        response = api_client.post(
            f"/api/programs/{program.program_id}/opportunities/",
            payload,
            format="json",
        )
        assert response.status_code == 400

    @patch("commcare_connect.opportunity.tasks.get_connect_blocks_for_app")
    @patch("commcare_connect.opportunity.tasks.get_deliver_units_for_app")
    def test_create_opportunity_registers_new_api_key(
        self,
        mock_deliver_units,
        mock_learn_modules,
        mock_hq_apps,
        api_client,
        program_manager_org_user_admin,
        program,
        organization,
        accepted_application,
        hq_server,
    ):
        """If the caller supplies an api_key string that doesn't exist yet, it gets created for them."""
        mock_learn_modules.return_value = []
        mock_deliver_units.return_value = [DeliverUnitData(id="du-1", name="DU 1")]

        # Note: no hq_api_key fixture used — we're supplying a fresh key string
        fresh_key = "freshly-minted-api-key-12345"
        assert not HQApiKey.objects.filter(api_key=fresh_key).exists()

        api_client.force_authenticate(program_manager_org_user_admin)
        payload = _opportunity_payload(organization, hq_server, type("K", (), {"api_key": fresh_key})())
        response = api_client.post(
            f"/api/programs/{program.program_id}/opportunities/",
            payload,
            format="json",
        )
        assert response.status_code == 201
        key = HQApiKey.objects.get(api_key=fresh_key)
        assert key.user == program_manager_org_user_admin
        assert key.hq_server == hq_server

    @patch("commcare_connect.program.api.serializers.get_applications_for_user_by_domain")
    @patch("commcare_connect.opportunity.tasks.get_connect_blocks_for_app")
    @patch("commcare_connect.opportunity.tasks.get_deliver_units_for_app")
    def test_create_opportunity_app_not_found_in_hq(
        self,
        mock_deliver_units,
        mock_learn_modules,
        mock_hq_apps,
        api_client,
        program_manager_org_user_admin,
        program,
        organization,
        accepted_application,
        hq_server,
        hq_api_key,
    ):
        # Return apps list that doesn't include our cc_app_ids
        mock_hq_apps.return_value = [{"id": "some-other-app", "name": "Other"}]
        api_client.force_authenticate(program_manager_org_user_admin)
        response = api_client.post(
            f"/api/programs/{program.program_id}/opportunities/",
            _opportunity_payload(organization, hq_server, hq_api_key),
            format="json",
        )
        assert response.status_code == 400

    @patch("commcare_connect.program.api.serializers.get_applications_for_user_by_domain")
    @patch("commcare_connect.opportunity.tasks.get_connect_blocks_for_app")
    @patch("commcare_connect.opportunity.tasks.get_deliver_units_for_app")
    def test_create_opportunity_rolls_back_when_hq_sync_fails(
        self,
        mock_deliver_units,
        mock_learn_modules,
        mock_hq_apps,
        api_client,
        program_manager_org_user_admin,
        program,
        organization,
        accepted_application,
        hq_server,
        hq_api_key,
    ):
        """If the HQ sync step fails, the opportunity + apps should be rolled back."""
        mock_hq_apps.return_value = [
            {"id": "learn-app-123", "name": "Test Learn App"},
            {"id": "deliver-app-456", "name": "Test Deliver App"},
        ]
        # Name lookup succeeds, but the sync step fails with a network error
        mock_learn_modules.side_effect = httpx.ConnectError("connection refused")

        api_client.force_authenticate(program_manager_org_user_admin)
        response = api_client.post(
            f"/api/programs/{program.program_id}/opportunities/",
            _opportunity_payload(organization, hq_server, hq_api_key),
            format="json",
        )
        assert response.status_code == 502
        assert not ManagedOpportunity.objects.filter(program=program).exists()
        assert not CommCareApp.objects.filter(cc_app_id="learn-app-123").exists()
        assert not CommCareApp.objects.filter(cc_app_id="deliver-app-456").exists()
