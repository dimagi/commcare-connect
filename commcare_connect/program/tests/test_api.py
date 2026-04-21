import pytest

from commcare_connect.opportunity.tests.factories import DeliveryTypeFactory


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
