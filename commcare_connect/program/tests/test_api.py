import pytest

from commcare_connect.opportunity.tests.factories import DeliveryTypeFactory
from commcare_connect.program.models import Program


@pytest.fixture
def delivery_type(db):
    return DeliveryTypeFactory(slug="test-delivery")


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
