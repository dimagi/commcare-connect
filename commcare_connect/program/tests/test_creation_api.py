import datetime

import pytest
from django.utils.timezone import now
from rest_framework.test import APIClient

from commcare_connect.commcarehq.tests.factories import HQServerFactory
from commcare_connect.opportunity.models import Country, Currency, DeliveryType
from commcare_connect.opportunity.tests.factories import CommCareAppFactory, HQApiKeyFactory
from commcare_connect.organization.models import Organization
from commcare_connect.program.models import ManagedOpportunity, Program, ProgramApplication, ProgramApplicationStatus
from commcare_connect.program.tests.factories import ProgramFactory
from commcare_connect.users.models import User
from commcare_connect.users.tests.factories import OrganizationFactory


def _add_create_credentials(api_client, user):
    """Add OAuth token with 'create' scope to the API client."""
    token, _ = user.oauth2_provider_accesstoken.get_or_create(
        token=f"create-token-{user.pk}",
        scope="read write create",
        defaults={"expires": now() + datetime.timedelta(hours=1)},
    )
    api_client.credentials(Authorization=f"Bearer {token}")


@pytest.mark.django_db
class TestCreateProgram:
    def _payload(self, org, delivery_type_id):
        return {
            "name": "Test Program",
            "description": "A test program",
            "delivery_type": delivery_type_id,
            "budget": 50000,
            "currency": "USD",
            "country": "USA",
            "start_date": "2026-04-01",
            "end_date": "2026-12-31",
            "organization": org.slug,
        }

    def test_create_program_success(
        self, api_client: APIClient, program_manager_org_user_admin: User, program_manager_org: Organization
    ):
        dt = DeliveryType.objects.create(name="Direct", slug="direct", description="Direct delivery")
        _add_create_credentials(api_client, program_manager_org_user_admin)
        response = api_client.post("/api/program/", self._payload(program_manager_org, dt.id), format="json")
        assert response.status_code == 201
        assert Program.objects.filter(name="Test Program").exists()
        program = Program.objects.get(name="Test Program")
        assert program.organization == program_manager_org
        assert program.slug

    def test_create_program_non_pm_org_forbidden(
        self, api_client: APIClient, org_user_admin: User, organization: Organization
    ):
        dt = DeliveryType.objects.create(name="Direct", slug="direct", description="Direct delivery")
        _add_create_credentials(api_client, org_user_admin)
        response = api_client.post("/api/program/", self._payload(organization, dt.id), format="json")
        assert response.status_code == 403

    def test_create_program_missing_fields(
        self, api_client: APIClient, program_manager_org_user_admin: User, program_manager_org: Organization
    ):
        _add_create_credentials(api_client, program_manager_org_user_admin)
        response = api_client.post(
            "/api/program/", {"name": "Incomplete", "organization": program_manager_org.slug}, format="json"
        )
        assert response.status_code == 400

    def test_create_program_end_date_before_start(
        self, api_client: APIClient, program_manager_org_user_admin: User, program_manager_org: Organization
    ):
        dt = DeliveryType.objects.create(name="Direct", slug="direct", description="Direct delivery")
        payload = self._payload(program_manager_org, dt.id)
        payload["start_date"] = "2026-12-31"
        payload["end_date"] = "2026-01-01"
        _add_create_credentials(api_client, program_manager_org_user_admin)
        response = api_client.post("/api/program/", payload, format="json")
        assert response.status_code == 400


@pytest.mark.django_db
class TestListPrograms:
    def test_list_programs_scoped_to_user_orgs(
        self, api_client: APIClient, program_manager_org_user_admin: User, program_manager_org: Organization
    ):
        ProgramFactory(organization=program_manager_org)
        ProgramFactory()  # different org - user has no membership, should not appear
        api_client.force_authenticate(program_manager_org_user_admin)
        response = api_client.get("/api/program/")
        assert response.status_code == 200
        assert len(response.data) == 1  # only sees programs from their own org

    def test_list_programs_filtered_by_org(
        self, api_client: APIClient, program_manager_org_user_admin: User, program_manager_org: Organization
    ):
        ProgramFactory(organization=program_manager_org)
        api_client.force_authenticate(program_manager_org_user_admin)
        response = api_client.get(f"/api/program/?organization={program_manager_org.slug}")
        assert response.status_code == 200
        assert len(response.data) == 1

    def test_list_programs_no_scope_required_for_read(
        self, api_client: APIClient, program_manager_org_user_admin: User, program_manager_org: Organization
    ):
        """GET requests don't require the 'create' OAuth scope - just authentication."""
        ProgramFactory(organization=program_manager_org)
        api_client.force_authenticate(program_manager_org_user_admin)
        response = api_client.get("/api/program/")
        assert response.status_code == 200


@pytest.mark.django_db
class TestCreateManagedOpportunity:
    @pytest.fixture
    def setup_program_with_nm(self, program_manager_org, program_manager_org_user_admin):
        dt = DeliveryType.objects.create(name="Direct", slug="direct-mo", description="Direct")
        currency = Currency.objects.get_or_create(code="USD", defaults={"name": "US Dollar"})[0]
        country = Country.objects.get_or_create(code="USA", defaults={"name": "USA", "currency": currency})[0]
        program = Program.objects.create(
            name="Test Program",
            description="Test",
            delivery_type=dt,
            budget=100000,
            currency=currency,
            country=country,
            start_date="2026-01-01",
            end_date="2026-12-31",
            organization=program_manager_org,
            created_by=program_manager_org_user_admin.email,
            modified_by=program_manager_org_user_admin.email,
        )
        nm_org = OrganizationFactory()
        ProgramApplication.objects.create(
            program=program,
            organization=nm_org,
            status=ProgramApplicationStatus.ACCEPTED,
            created_by="test@test.com",
            modified_by="test@test.com",
        )
        return program, nm_org

    def test_create_managed_opportunity_success(
        self,
        api_client: APIClient,
        program_manager_org_user_admin: User,
        program_manager_org: Organization,
        setup_program_with_nm,
    ):
        program, nm_org = setup_program_with_nm
        hq_server = HQServerFactory()
        api_key = HQApiKeyFactory(hq_server=hq_server, user=program_manager_org_user_admin)
        learn_app = CommCareAppFactory(organization=nm_org, hq_server=hq_server)
        deliver_app = CommCareAppFactory(organization=nm_org, hq_server=hq_server)

        _add_create_credentials(api_client, program_manager_org_user_admin)
        response = api_client.post(
            f"/api/program/{program.program_id}/opportunity/",
            {
                "name": "Test Opp",
                "description": "A test opportunity",
                "short_description": "Test",
                "organization": nm_org.slug,
                "learn_app": learn_app.id,
                "deliver_app": deliver_app.id,
                "start_date": "2026-04-01",
                "end_date": "2026-12-31",
                "total_budget": 50000,
                "api_key": api_key.id,
            },
            format="json",
        )
        assert response.status_code == 201, response.data
        opp = ManagedOpportunity.objects.get(name="Test Opp")
        assert opp.program == program
        assert opp.organization == nm_org
        assert opp.currency == program.currency
        assert opp.delivery_type == program.delivery_type
        assert opp.managed is True

    def test_create_managed_opportunity_non_accepted_org_fails(
        self,
        api_client: APIClient,
        program_manager_org_user_admin: User,
        program_manager_org: Organization,
        setup_program_with_nm,
    ):
        program, nm_org = setup_program_with_nm
        other_org = OrganizationFactory()
        hq_server = HQServerFactory()
        api_key = HQApiKeyFactory(hq_server=hq_server, user=program_manager_org_user_admin)
        learn_app = CommCareAppFactory(organization=other_org, hq_server=hq_server)
        deliver_app = CommCareAppFactory(organization=other_org, hq_server=hq_server)

        _add_create_credentials(api_client, program_manager_org_user_admin)
        response = api_client.post(
            f"/api/program/{program.program_id}/opportunity/",
            {
                "name": "Test Opp",
                "description": "A test opportunity",
                "organization": other_org.slug,
                "learn_app": learn_app.id,
                "deliver_app": deliver_app.id,
                "start_date": "2026-04-01",
                "end_date": "2026-12-31",
                "total_budget": 50000,
                "api_key": api_key.id,
            },
            format="json",
        )
        assert response.status_code == 400


@pytest.mark.django_db
class TestProgramApplicationAPI:
    @pytest.fixture
    def program_setup(self, program_manager_org, program_manager_org_user_admin):
        program = ProgramFactory(organization=program_manager_org)
        return program

    def test_invite_org_to_program(
        self,
        api_client: APIClient,
        program_manager_org_user_admin: User,
        program_manager_org: Organization,
        program_setup,
    ):
        program = program_setup
        target_org = OrganizationFactory()
        _add_create_credentials(api_client, program_manager_org_user_admin)
        response = api_client.post(
            f"/api/program/{program.program_id}/applications/",
            {"organization": target_org.slug},
            format="json",
        )
        assert response.status_code == 201, response.data
        assert ProgramApplication.objects.filter(
            program=program, organization=target_org, status=ProgramApplicationStatus.INVITED
        ).exists()

    def test_cannot_invite_own_org(
        self,
        api_client: APIClient,
        program_manager_org_user_admin: User,
        program_manager_org: Organization,
        program_setup,
    ):
        program = program_setup
        _add_create_credentials(api_client, program_manager_org_user_admin)
        response = api_client.post(
            f"/api/program/{program.program_id}/applications/",
            {"organization": program_manager_org.slug},
            format="json",
        )
        assert response.status_code == 400

    def test_list_applications(
        self,
        api_client: APIClient,
        program_manager_org_user_admin: User,
        program_manager_org: Organization,
        program_setup,
    ):
        program = program_setup
        target_org = OrganizationFactory()
        ProgramApplication.objects.create(
            program=program,
            organization=target_org,
            status=ProgramApplicationStatus.INVITED,
            created_by="test@test.com",
            modified_by="test@test.com",
        )
        api_client.force_authenticate(program_manager_org_user_admin)
        response = api_client.get(f"/api/program/{program.program_id}/applications/")
        assert response.status_code == 200
        assert len(response.data) == 1
