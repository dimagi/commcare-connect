import pytest
from unittest.mock import Mock, patch
from django.contrib.auth.models import AnonymousUser
from rest_framework.test import APIClient
from rest_framework.exceptions import NotFound

from commcare_connect.data_export.views import (
    _get_opportunity_or_404,
    _get_program_or_404,
    _get_org_or_404,
    BaseDataExportView,
    OpportunityDataExportView,
    ProgramOpportunityOrganizationDataView,
    SingleOpportunityDataView,
    OpportunityUserDataView,
    UserVisitDataView,
    CompletedWorkDataView,
    PaymentDataView,
    InvoiceDataView,
    CompletedModuleDataView,
    AssessmentDataView,
    LabsRecordDataView,
    ImageView,
)
from commcare_connect.opportunity.models import (
    BlobMeta,
    CompletedWork,
    LabsRecord,
    OpportunityAccess,
    Payment,
    PaymentInvoice,
    UserVisit,
)
from commcare_connect.opportunity.tests.factories import (
    CompletedModuleFactory,
    CompletedWorkFactory,
    OpportunityAccessFactory,
    OpportunityFactory,
    PaymentFactory,
    PaymentInvoiceFactory,
    PaymentUnitFactory,
    UserVisitFactory,
)
from commcare_connect.organization.models import Organization, OrganizationMembership
from commcare_connect.program.models import Program
from commcare_connect.users.models import User
from commcare_connect.users.tests.factories import MobileUserFactory, OrganizationFactory, UserFactory


@pytest.mark.django_db
class TestHelperFunctions:
    def test_get_opportunity_or_404_success(self):
        user = UserFactory()
        organization = OrganizationFactory()
        OrganizationMembership.objects.create(user=user, organization=organization)
        opportunity = OpportunityFactory(organization=organization)

        result = _get_opportunity_or_404(user, opportunity.id)
        assert result == opportunity

    def test_get_opportunity_or_404_raises_not_found(self):
        user = UserFactory()
        with pytest.raises(NotFound):
            _get_opportunity_or_404(user, 99999)

    def test_get_program_or_404_success(self, program):
        user = UserFactory()
        OrganizationMembership.objects.create(user=user, organization=program.organization)

        result = _get_program_or_404(user, program.id)
        assert result == program

    def test_get_program_or_404_raises_not_found(self):
        user = UserFactory()
        with pytest.raises(NotFound):
            _get_program_or_404(user, 99999)

    def test_get_org_or_404_success(self):
        user = UserFactory()
        organization = OrganizationFactory()
        OrganizationMembership.objects.create(user=user, organization=organization)

        result = _get_org_or_404(user, organization.id)
        assert result == organization

    def test_get_org_or_404_raises_not_found(self):
        user = UserFactory()
        with pytest.raises(NotFound):
            _get_org_or_404(user, 99999)


@pytest.mark.django_db
class TestProgramOpportunityOrganizationDataView:
    def test_get_returns_organizations_opportunities_programs(self, api_client):
        user = UserFactory()
        organization = OrganizationFactory()
        OrganizationMembership.objects.create(user=user, organization=organization)
        opportunity = OpportunityFactory(organization=organization)

        api_client.force_authenticate(user=user)
        response = api_client.get("/api/export/program_opportunity_organization/")

        assert response.status_code == 200
        data = response.json()
        assert "organizations" in data
        assert "opportunities" in data
        assert "programs" in data
        assert len(data["organizations"]) >= 1
        assert len(data["opportunities"]) >= 1


@pytest.mark.django_db
class TestSingleOpportunityDataView:
    def test_get_object_success(self, api_client):
        user = UserFactory()
        organization = OrganizationFactory()
        OrganizationMembership.objects.create(user=user, organization=organization)
        opportunity = OpportunityFactory(organization=organization)
        PaymentUnitFactory(opportunity=opportunity)

        api_client.force_authenticate(user=user)
        response = api_client.get(f"/api/export/opportunity/{opportunity.id}/")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == opportunity.id
        assert data["name"] == opportunity.name

    def test_get_object_not_found(self, api_client):
        user = UserFactory()
        api_client.force_authenticate(user=user)
        response = api_client.get("/api/export/opportunity/99999/")

        assert response.status_code == 404


@pytest.mark.django_db
class TestOpportunityUserDataView:
    def test_get_queryset(self, api_client):
        user = UserFactory()
        organization = OrganizationFactory()
        OrganizationMembership.objects.create(user=user, organization=organization)
        opportunity = OpportunityFactory(organization=organization)
        access = OpportunityAccessFactory(opportunity=opportunity)

        api_client.force_authenticate(user=user)
        response = api_client.get(f"/api/export/opportunity/{opportunity.id}/users/")

        assert response.status_code == 200


@pytest.mark.django_db
class TestUserVisitDataView:
    def test_get_queryset(self, api_client):
        user = UserFactory()
        organization = OrganizationFactory()
        OrganizationMembership.objects.create(user=user, organization=organization)
        opportunity = OpportunityFactory(organization=organization)
        visit = UserVisitFactory(opportunity=opportunity)

        api_client.force_authenticate(user=user)
        response = api_client.get(f"/api/export/opportunity/{opportunity.id}/visits/")

        assert response.status_code == 200


@pytest.mark.django_db
class TestCompletedWorkDataView:
    def test_get_queryset(self, api_client):
        user = UserFactory()
        organization = OrganizationFactory()
        OrganizationMembership.objects.create(user=user, organization=organization)
        opportunity = OpportunityFactory(organization=organization)
        access = OpportunityAccessFactory(opportunity=opportunity)
        completed_work = CompletedWorkFactory(opportunity_access=access)

        api_client.force_authenticate(user=user)
        response = api_client.get(f"/api/export/opportunity/{opportunity.id}/completed_work/")

        assert response.status_code == 200


@pytest.mark.django_db
class TestPaymentDataView:
    def test_get_queryset(self, api_client):
        user = UserFactory()
        organization = OrganizationFactory()
        OrganizationMembership.objects.create(user=user, organization=organization)
        opportunity = OpportunityFactory(organization=organization)
        access = OpportunityAccessFactory(opportunity=opportunity)
        payment = PaymentFactory(opportunity_access=access)

        api_client.force_authenticate(user=user)
        response = api_client.get(f"/api/export/opportunity/{opportunity.id}/payments/")

        assert response.status_code == 200


@pytest.mark.django_db
class TestInvoiceDataView:
    def test_get_queryset(self, api_client):
        user = UserFactory()
        organization = OrganizationFactory()
        OrganizationMembership.objects.create(user=user, organization=organization)
        opportunity = OpportunityFactory(organization=organization)
        invoice = PaymentInvoiceFactory(opportunity=opportunity)

        api_client.force_authenticate(user=user)
        response = api_client.get(f"/api/export/opportunity/{opportunity.id}/invoices/")

        assert response.status_code == 200


@pytest.mark.django_db
class TestCompletedModuleDataView:
    def test_get_queryset(self, api_client):
        user = UserFactory()
        organization = OrganizationFactory()
        OrganizationMembership.objects.create(user=user, organization=organization)
        opportunity = OpportunityFactory(organization=organization)
        module = CompletedModuleFactory(opportunity=opportunity)

        api_client.force_authenticate(user=user)
        response = api_client.get(f"/api/export/opportunity/{opportunity.id}/modules/")

        assert response.status_code == 200

    def test_get_queryset_filtered_by_username(self, api_client):
        user = UserFactory()
        organization = OrganizationFactory()
        OrganizationMembership.objects.create(user=user, organization=organization)
        opportunity = OpportunityFactory(organization=organization)
        mobile_user = MobileUserFactory()
        access = OpportunityAccessFactory(opportunity=opportunity, user=mobile_user)
        module = CompletedModuleFactory(opportunity=opportunity, opportunity_access=access, user=mobile_user)

        api_client.force_authenticate(user=user)
        response = api_client.get(
            f"/api/export/opportunity/{opportunity.id}/modules/?username={mobile_user.username}"
        )

        assert response.status_code == 200


@pytest.mark.django_db
class TestAssessmentDataView:
    def test_get_queryset(self, api_client):
        user = UserFactory()
        organization = OrganizationFactory()
        OrganizationMembership.objects.create(user=user, organization=organization)
        opportunity = OpportunityFactory(organization=organization)

        api_client.force_authenticate(user=user)
        response = api_client.get(f"/api/export/opportunity/{opportunity.id}/assessments/")

        assert response.status_code == 200


@pytest.mark.django_db
class TestLabsRecordDataView:
    def test_get_queryset_by_opportunity(self, api_client):
        user = UserFactory()
        organization = OrganizationFactory()
        OrganizationMembership.objects.create(user=user, organization=organization)
        opportunity = OpportunityFactory(organization=organization)
        labs_record = LabsRecord.objects.create(
            opportunity=opportunity,
            organization=organization,
            experiment="test",
            type="test_type",
            data={},
        )

        api_client.force_authenticate(user=user)
        response = api_client.get(f"/api/export/labs/?opportunity_id={opportunity.id}")

        assert response.status_code == 200

    def test_get_queryset_public(self, api_client):
        user = UserFactory()
        organization = OrganizationFactory()
        labs_record = LabsRecord.objects.create(
            organization=organization,
            experiment="test",
            type="test_type",
            data={},
            public=True,
        )

        api_client.force_authenticate(user=user)
        response = api_client.get("/api/export/labs/")

        assert response.status_code == 200

    def test_create_labs_record(self, api_client):
        user = UserFactory()
        organization = OrganizationFactory()
        OrganizationMembership.objects.create(user=user, organization=organization)
        opportunity = OpportunityFactory(organization=organization)

        api_client.force_authenticate(user=user)
        data = {
            "opportunity_id": opportunity.id,
            "organization_id": organization.id,
            "experiment": "test_experiment",
            "type": "test_type",
            "data": {"key": "value"},
        }
        response = api_client.post("/api/export/labs/", data=data, format="json")

        assert response.status_code == 200

    def test_create_labs_record_upsert(self, api_client):
        user = UserFactory()
        organization = OrganizationFactory()
        OrganizationMembership.objects.create(user=user, organization=organization)
        opportunity = OpportunityFactory(organization=organization)

        existing_record = LabsRecord.objects.create(
            opportunity=opportunity,
            organization=organization,
            experiment="test",
            type="test_type",
            data={"old": "data"},
        )

        api_client.force_authenticate(user=user)
        data = {
            "id": existing_record.id,
            "opportunity_id": opportunity.id,
            "organization_id": organization.id,
            "experiment": "test_experiment_updated",
            "type": "test_type_updated",
            "data": {"new": "data"},
        }
        response = api_client.post("/api/export/labs/", data=data, format="json")

        assert response.status_code == 200
        existing_record.refresh_from_db()
        assert existing_record.experiment == "test_experiment_updated"

    def test_delete_labs_record(self, api_client):
        user = UserFactory()
        organization = OrganizationFactory()
        OrganizationMembership.objects.create(user=user, organization=organization)

        labs_record = LabsRecord.objects.create(
            organization=organization,
            experiment="test",
            type="test_type",
            data={},
        )

        api_client.force_authenticate(user=user)
        data = [{"id": labs_record.id, "organization_id": organization.id}]
        response = api_client.delete("/api/export/labs/", data=data, format="json")

        assert response.status_code == 200
        assert not LabsRecord.objects.filter(id=labs_record.id).exists()


@pytest.mark.django_db
class TestBaseStreamingCSVExportView:
    def test_get_data_generator(self, api_client):
        user = UserFactory()
        organization = OrganizationFactory()
        OrganizationMembership.objects.create(user=user, organization=organization)
        opportunity = OpportunityFactory(organization=organization)

        api_client.force_authenticate(user=user)
        response = api_client.get(f"/api/export/opportunity/{opportunity.id}/users/")

        assert response.status_code == 200
        assert response["Content-Type"] == "text/csv"


@pytest.mark.django_db
class TestImageView:
    @patch("commcare_connect.data_export.views.storages")
    def test_get_image(self, mock_storages, api_client):
        user = UserFactory()
        organization = OrganizationFactory()
        OrganizationMembership.objects.create(user=user, organization=organization)
        opportunity = OpportunityFactory(organization=organization)
        visit = UserVisitFactory(opportunity=opportunity)

        blob_meta = BlobMeta.objects.create(
            blob_id="test-blob-id",
            parent_id=visit.xform_id,
            name="test.jpg",
            content_type="image/jpeg",
            content_length=1024,
        )

        # Mock the storage
        mock_file = Mock()
        mock_storages.__getitem__.return_value.open.return_value = mock_file

        api_client.force_authenticate(user=user)
        response = api_client.get(f"/api/export/opportunity/{opportunity.id}/image/?blob_id={blob_meta.blob_id}")

        assert response.status_code == 200