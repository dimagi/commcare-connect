import pytest

from commcare_connect.commcarehq.tests.factories import HQServerFactory
from commcare_connect.opportunity.models import PaymentUnit
from commcare_connect.opportunity.tests.factories import CommCareAppFactory, DeliverUnitFactory, PaymentUnitFactory
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
