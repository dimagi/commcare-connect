import datetime
from unittest.mock import patch

import pytest
from django.utils.timezone import now
from rest_framework.test import APIClient

from commcare_connect.commcarehq.tests.factories import HQServerFactory
from commcare_connect.opportunity.models import DeliverUnit, DeliveryType, PaymentUnit
from commcare_connect.opportunity.tests.factories import CommCareAppFactory
from commcare_connect.organization.models import Organization
from commcare_connect.program.models import ManagedOpportunity, ProgramApplication, ProgramApplicationStatus
from commcare_connect.program.tests.factories import ProgramFactory
from commcare_connect.users.models import User
from commcare_connect.users.tests.factories import OrganizationFactory


def _add_create_credentials(api_client, user):
    """Add OAuth token with 'create' scope to the API client. Mirrors _add_export_credentials pattern."""
    token, _ = user.oauth2_provider_accesstoken.get_or_create(
        token=f"create-token-{user.pk}",
        scope="read write create",
        defaults={"expires": now() + datetime.timedelta(hours=1)},
    )
    api_client.credentials(Authorization=f"Bearer {token}")


@pytest.mark.django_db
class TestOrgPermissions:
    def test_unauthenticated_rejected(self, api_client: APIClient):
        response = api_client.get("/api/lookups/delivery_types/")
        assert response.status_code == 401

    def test_no_create_scope_rejected(
        self, api_client: APIClient, program_manager_org_user_admin: User, program_manager_org: Organization
    ):
        """Token without 'create' scope cannot access creation endpoints."""
        api_client.force_authenticate(program_manager_org_user_admin)
        response = api_client.post(
            "/api/program/",
            {
                "name": "Test",
                "description": "Test",
                "delivery_type": 1,
                "budget": 1000,
                "currency": "USD",
                "country": "USA",
                "start_date": "2026-01-01",
                "end_date": "2026-12-31",
                "organization": program_manager_org.slug,
            },
            format="json",
        )
        assert response.status_code == 403

    def test_non_pm_org_rejected(self, api_client: APIClient, org_user_admin: User, organization: Organization):
        """Non-program-manager org admin cannot access creation endpoints."""
        _add_create_credentials(api_client, org_user_admin)
        response = api_client.post(
            "/api/program/",
            {
                "name": "Test",
                "description": "Test",
                "delivery_type": 1,
                "budget": 1000,
                "currency": "USD",
                "country": "USA",
                "start_date": "2026-01-01",
                "end_date": "2026-12-31",
                "organization": organization.slug,
            },
            format="json",
        )
        assert response.status_code == 403

    def test_member_role_rejected(
        self, api_client: APIClient, program_manager_org_user_member: User, program_manager_org: Organization
    ):
        """Member (not admin) of PM org cannot access creation endpoints."""
        _add_create_credentials(api_client, program_manager_org_user_member)
        response = api_client.post(
            "/api/program/",
            {
                "name": "Test",
                "description": "Test",
                "delivery_type": 1,
                "budget": 1000,
                "currency": "USD",
                "country": "USA",
                "start_date": "2026-01-01",
                "end_date": "2026-12-31",
                "organization": program_manager_org.slug,
            },
            format="json",
        )
        assert response.status_code == 403


@pytest.mark.django_db
class TestLookupEndpoints:
    def test_list_delivery_types(self, api_client: APIClient, program_manager_org_user_admin: User):
        DeliveryType.objects.create(name="Direct Delivery", slug="direct-delivery", description="Direct")
        api_client.force_authenticate(program_manager_org_user_admin)
        response = api_client.get("/api/lookups/delivery_types/")
        assert response.status_code == 200
        assert len(response.data) >= 1
        assert "id" in response.data[0]
        assert "name" in response.data[0]
        assert "slug" in response.data[0]

    def test_list_currencies(self, api_client: APIClient, program_manager_org_user_admin: User):
        api_client.force_authenticate(program_manager_org_user_admin)
        response = api_client.get("/api/lookups/currencies/")
        assert response.status_code == 200
        assert len(response.data) >= 1
        assert "code" in response.data[0]
        assert "name" in response.data[0]

    def test_list_countries(self, api_client: APIClient, program_manager_org_user_admin: User):
        api_client.force_authenticate(program_manager_org_user_admin)
        response = api_client.get("/api/lookups/countries/")
        assert response.status_code == 200
        assert len(response.data) >= 1
        assert "code" in response.data[0]
        assert "name" in response.data[0]
        assert "currency" in response.data[0]

    def test_lookups_unauthenticated(self, api_client: APIClient):
        response = api_client.get("/api/lookups/delivery_types/")
        assert response.status_code == 401


@pytest.fixture
def managed_opp_setup(program_manager_org, program_manager_org_user_admin):
    """Module-level fixture: creates a fully configured ManagedOpportunity for testing."""
    from commcare_connect.opportunity.models import Country, Currency

    dt = DeliveryType.objects.create(name="Direct PU", slug="direct-pu", description="Direct")
    currency = Currency.objects.get_or_create(code="USD", defaults={"name": "US Dollar"})[0]
    country = Country.objects.get_or_create(code="USA", defaults={"name": "USA", "currency": currency})[0]
    program = ProgramFactory(organization=program_manager_org, delivery_type=dt, currency=currency, country=country)
    nm_org = OrganizationFactory()
    ProgramApplication.objects.create(
        program=program,
        organization=nm_org,
        status=ProgramApplicationStatus.ACCEPTED,
        created_by="test@test.com",
        modified_by="test@test.com",
    )
    hq_server = HQServerFactory()
    opp = ManagedOpportunity.objects.create(
        name="Test Opp",
        description="test",
        program=program,
        organization=nm_org,
        currency=currency,
        country=country,
        delivery_type=dt,
        managed=True,
        start_date="2026-01-01",
        end_date="2026-12-31",
        total_budget=50000,
        created_by="test@test.com",
        modified_by="test@test.com",
        learn_app=CommCareAppFactory(organization=nm_org, hq_server=hq_server),
        deliver_app=CommCareAppFactory(organization=nm_org, hq_server=hq_server),
        hq_server=hq_server,
    )
    return opp


@pytest.mark.django_db
class TestPaymentUnitAPI:
    def test_create_payment_unit(self, api_client: APIClient, program_manager_org_user_admin: User, managed_opp_setup):
        opp = managed_opp_setup
        _add_create_credentials(api_client, program_manager_org_user_admin)
        response = api_client.post(
            f"/api/opportunity/{opp.opportunity_id}/payment_units/",
            {
                "name": "Per Visit",
                "description": "Payment per visit",
                "amount": 100,
                "org_amount": 20,
                "max_total": 50,
                "max_daily": 5,
                "organization": opp.program.organization.slug,
            },
            format="json",
        )
        assert response.status_code == 201, response.data
        assert PaymentUnit.objects.filter(opportunity=opp, name="Per Visit").exists()

    def test_list_payment_units(self, api_client: APIClient, program_manager_org_user_admin: User, managed_opp_setup):
        opp = managed_opp_setup
        PaymentUnit.objects.create(
            opportunity=opp, name="Unit 1", description="test", amount=10, max_total=10, max_daily=5
        )
        api_client.force_authenticate(program_manager_org_user_admin)
        response = api_client.get(f"/api/opportunity/{opp.opportunity_id}/payment_units/")
        assert response.status_code == 200
        assert len(response.data) == 1


@pytest.mark.django_db
class TestDeliverUnitAPI:
    @pytest.fixture
    def opp_with_payment_unit(self, managed_opp_setup):
        opp = managed_opp_setup
        pu = PaymentUnit.objects.create(
            opportunity=opp, name="Per Visit", description="test", amount=10, max_total=10, max_daily=5
        )
        return opp, pu

    def test_create_deliver_unit(
        self, api_client: APIClient, program_manager_org_user_admin: User, opp_with_payment_unit
    ):
        opp, pu = opp_with_payment_unit
        _add_create_credentials(api_client, program_manager_org_user_admin)
        response = api_client.post(
            f"/api/opportunity/{opp.opportunity_id}/deliver_units/",
            {
                "slug": "form_1",
                "name": "Home Visit Form",
                "payment_unit": pu.id,
                "app": opp.deliver_app.id,
                "optional": False,
                "organization": opp.program.organization.slug,
            },
            format="json",
        )
        assert response.status_code == 201, response.data
        assert DeliverUnit.objects.filter(slug="form_1", payment_unit=pu).exists()

    def test_list_deliver_units(
        self, api_client: APIClient, program_manager_org_user_admin: User, opp_with_payment_unit
    ):
        opp, pu = opp_with_payment_unit
        DeliverUnit.objects.create(app=opp.deliver_app, slug="form_1", name="Form 1", payment_unit=pu)
        api_client.force_authenticate(program_manager_org_user_admin)
        response = api_client.get(f"/api/opportunity/{opp.opportunity_id}/deliver_units/")
        assert response.status_code == 200
        assert len(response.data) == 1
        assert "id" in response.data[0]
        assert "slug" in response.data[0]


@pytest.mark.django_db
class TestInviteUsersAPI:
    @pytest.fixture
    def active_opp(self, managed_opp_setup):
        """Set up a fully configured opportunity with payment units."""
        opp = managed_opp_setup
        PaymentUnit.objects.create(
            opportunity=opp, name="Per Visit", description="test", amount=10, max_total=10, max_daily=5
        )
        opp.active = True
        opp.save()
        return opp

    @patch("commcare_connect.opportunity.api.views.add_connect_users")
    def test_invite_users_success(
        self, mock_add_users, api_client: APIClient, program_manager_org_user_admin: User, active_opp
    ):
        opp = active_opp
        _add_create_credentials(api_client, program_manager_org_user_admin)
        response = api_client.post(
            f"/api/opportunity/{opp.opportunity_id}/invite/",
            {
                "phone_numbers": ["+1234567890", "+0987654321"],
                "organization": opp.program.organization.slug,
            },
            format="json",
        )
        assert response.status_code == 200, response.data
        assert response.data["invited"] == 2
        mock_add_users.delay.assert_called_once_with(["+1234567890", "+0987654321"], str(opp.pk))

    def test_invite_invalid_phone_number(
        self, api_client: APIClient, program_manager_org_user_admin: User, active_opp
    ):
        opp = active_opp
        _add_create_credentials(api_client, program_manager_org_user_admin)
        response = api_client.post(
            f"/api/opportunity/{opp.opportunity_id}/invite/",
            {
                "phone_numbers": ["not-a-number"],
                "organization": opp.program.organization.slug,
            },
            format="json",
        )
        assert response.status_code == 400
