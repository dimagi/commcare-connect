import datetime

import pytest

from commcare_connect.opportunity.tests.factories import DeliveryTypeFactory
from commcare_connect.program.models import Program, ProgramApplication, ProgramApplicationStatus
from commcare_connect.program.tests.factories import ProgramApplicationFactory, ProgramFactory


@pytest.fixture
def delivery_type(db):
    return DeliveryTypeFactory(slug="test-delivery")


@pytest.fixture
def program(program_manager_org, delivery_type):
    return ProgramFactory(
        organization=program_manager_org,
        start_date=datetime.date(2026, 1, 1),
        end_date=datetime.date(2026, 12, 31),
        budget=500000,
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
