import pytest
from decimal import Decimal
from django.utils import timezone

from commcare_connect.data_export.serializer import (
    AssessmentDataSerializer,
    CompletedModuleDataSerializer,
    CompletedWorkDataSerializer,
    InvoiceDataSerializer,
    LabsRecordDataSerializer,
    OpportunityDataExportSerializer,
    OpportunitySerializer,
    OpportunityUserDataSerializer,
    OrganizationDataExportSerializer,
    PaymentDataSerializer,
    ProgramDataExportSerializer,
    UserVisitDataSerialier,
)
from commcare_connect.opportunity.models import (
    Assessment,
    CompletedModule,
    CompletedWork,
    LabsRecord,
    Opportunity,
    OpportunityAccess,
    OpportunityClaimLimit,
    Payment,
    PaymentInvoice,
    UserVisit,
)
from commcare_connect.opportunity.tests.factories import (
    AssessmentFactory,
    CompletedModuleFactory,
    CompletedWorkFactory,
    LearnModuleFactory,
    OpportunityAccessFactory,
    OpportunityClaimFactory,
    OpportunityClaimLimitFactory,
    OpportunityFactory,
    PaymentFactory,
    PaymentInvoiceFactory,
    PaymentUnitFactory,
    UserVisitFactory,
)
from commcare_connect.organization.models import Organization
from commcare_connect.program.models import Program
from commcare_connect.users.models import User
from commcare_connect.users.tests.factories import MobileUserFactory, OrganizationFactory


@pytest.mark.django_db
class TestOpportunityDataExportSerializer:
    def test_basic_serialization(self):
        opportunity = OpportunityFactory()
        serializer = OpportunityDataExportSerializer(opportunity)
        data = serializer.data

        assert data["id"] == opportunity.id
        assert data["name"] == opportunity.name
        assert data["organization"] == opportunity.organization.slug
        assert "date_created" in data
        assert "end_date" in data
        assert "is_active" in data

    def test_get_program_managed_opportunity(self, managed_opportunity):
        serializer = OpportunityDataExportSerializer(managed_opportunity)
        data = serializer.data

        assert data["program"] == managed_opportunity.managedopportunity.program_id

    def test_get_program_non_managed_opportunity(self):
        opportunity = OpportunityFactory(managed=False)
        serializer = OpportunityDataExportSerializer(opportunity)
        data = serializer.data

        assert data["program"] is None

    def test_get_visit_count(self):
        opportunity = OpportunityFactory()
        # Annotate opportunity with visit_count
        opportunity.visit_count = 5
        serializer = OpportunityDataExportSerializer(opportunity)
        data = serializer.data

        assert data["visit_count"] == 5

    def test_get_org_pay_per_visit(self, managed_opportunity):
        expected_pay = 10
        managed_opportunity.managedopportunity.org_pay_per_visit = expected_pay
        managed_opportunity.managedopportunity.save()

        serializer = OpportunityDataExportSerializer(managed_opportunity)
        data = serializer.data

        assert data["org_pay_per_visit"] == expected_pay


@pytest.mark.django_db
class TestOrganizationDataExportSerializer:
    def test_serialization(self):
        organization = OrganizationFactory()
        serializer = OrganizationDataExportSerializer(organization)
        data = serializer.data

        assert data["id"] == organization.id
        assert data["slug"] == organization.slug
        assert data["name"] == organization.name


@pytest.mark.django_db
class TestProgramDataExportSerializer:
    def test_serialization(self, program):
        serializer = ProgramDataExportSerializer(program)
        data = serializer.data

        assert data["id"] == program.id
        assert data["name"] == program.name
        assert data["organization"] == program.organization.slug
        assert data["delivery_type"] == program.delivery_type.slug
        assert data["currency"] == program.currency_fk_id


@pytest.mark.django_db
class TestOpportunityUserDataSerializer:
    def test_serialization(self):
        access = OpportunityAccessFactory(accepted=True)
        claim = OpportunityClaimFactory(opportunity_access=access)
        payment_unit = PaymentUnitFactory(opportunity=access.opportunity)
        OpportunityClaimLimitFactory(opportunity_claim=claim, payment_unit=payment_unit)

        # Annotate the access object with required fields
        access.username = access.user.username
        access.name = access.user.name
        access.phone = access.user.phone_number
        access.user_invite_status = "accepted"
        access.date_claimed = claim.date_claimed

        serializer = OpportunityUserDataSerializer(access)
        data = serializer.data

        assert data["username"] == access.user.username
        assert data["name"] == access.user.name
        assert data["phone"] == access.user.phone_number

    def test_get_claim_limits(self):
        access = OpportunityAccessFactory()
        claim = OpportunityClaimFactory(opportunity_access=access)
        payment_unit = PaymentUnitFactory(opportunity=access.opportunity)
        claim_limit = OpportunityClaimLimitFactory(
            opportunity_claim=claim, payment_unit=payment_unit, max_visits=10
        )

        access.username = access.user.username
        serializer = OpportunityUserDataSerializer(access)
        data = serializer.data

        assert "claim_limits" in data
        assert len(data["claim_limits"]) == 1
        assert data["claim_limits"][0]["max_visits"] == 10


@pytest.mark.django_db
class TestUserVisitDataSerializer:
    def test_serialization(self):
        visit = UserVisitFactory()
        serializer = UserVisitDataSerialier(visit)
        data = serializer.data

        assert data["id"] == visit.id
        assert data["opportunity_id"] == visit.opportunity_id
        assert data["username"] == visit.user.username
        assert data["entity_id"] == visit.entity_id
        assert data["entity_name"] == visit.entity_name

    def test_get_images_empty(self):
        visit = UserVisitFactory()
        serializer = UserVisitDataSerialier(visit)
        data = serializer.data

        assert data["images"] == []


@pytest.mark.django_db
class TestCompletedWorkDataSerializer:
    def test_serialization(self):
        completed_work = CompletedWorkFactory()
        serializer = CompletedWorkDataSerializer(completed_work)
        data = serializer.data

        assert data["username"] == completed_work.opportunity_access.user.username
        assert data["opportunity_id"] == completed_work.opportunity_access.opportunity_id
        assert data["payment_unit_id"] == completed_work.payment_unit_id
        assert "status" in data
        assert "last_modified" in data


@pytest.mark.django_db
class TestPaymentDataSerializer:
    def test_serialization(self):
        payment = PaymentFactory()
        serializer = PaymentDataSerializer(payment)
        data = serializer.data

        assert data["username"] == payment.opportunity_access.user.username
        assert data["opportunity_id"] == payment.opportunity_access.opportunity_id
        assert data["amount"] == str(payment.amount)
        assert "created_at" in data


@pytest.mark.django_db
class TestInvoiceDataSerializer:
    def test_serialization(self):
        invoice = PaymentInvoiceFactory()
        serializer = InvoiceDataSerializer(invoice)
        data = serializer.data

        assert data["opportunity_id"] == invoice.opportunity_id
        assert data["amount"] == str(invoice.amount)
        assert "date" in data
        assert "invoice_number" in data


@pytest.mark.django_db
class TestAssessmentDataSerializer:
    def test_serialization(self):
        assessment = AssessmentFactory()
        serializer = AssessmentDataSerializer(assessment)
        data = serializer.data

        assert data["username"] == assessment.opportunity_access.user.username
        assert data["opportunity_id"] == assessment.opportunity_id
        assert data["score"] == assessment.score
        assert data["passed"] == assessment.passed


@pytest.mark.django_db
class TestLabsRecordDataSerializer:
    def test_serialization_with_user(self, mobile_user):
        opportunity = OpportunityFactory()
        labs_record = LabsRecord.objects.create(
            user=mobile_user,
            opportunity=opportunity,
            organization=opportunity.organization,
            experiment="test_experiment",
            type="test",
            data={"key": "value"},
            public=True,
        )

        serializer = LabsRecordDataSerializer(labs_record)
        data = serializer.data

        assert data["username"] == mobile_user.username
        assert data["opportunity_id"] == opportunity.id
        assert data["experiment"] == "test_experiment"

    def test_serialization_without_user(self):
        opportunity = OpportunityFactory()
        labs_record = LabsRecord.objects.create(
            user=None,
            opportunity=opportunity,
            organization=opportunity.organization,
            experiment="test_experiment",
            type="test",
            data={"key": "value"},
        )

        serializer = LabsRecordDataSerializer(labs_record)
        data = serializer.data

        assert data["username"] is None


@pytest.mark.django_db
class TestCompletedModuleDataSerializer:
    def test_serialization(self):
        completed_module = CompletedModuleFactory()
        serializer = CompletedModuleDataSerializer(completed_module)
        data = serializer.data

        assert data["username"] == completed_module.opportunity_access.user.username
        assert data["module"] == completed_module.module_id
        assert data["opportunity_id"] == completed_module.opportunity_id


@pytest.mark.django_db
class TestOpportunitySerializer:
    def test_serialization(self):
        opportunity = OpportunityFactory()
        PaymentUnitFactory(opportunity=opportunity)

        serializer = OpportunitySerializer(opportunity)
        data = serializer.data

        assert data["id"] == opportunity.id
        assert data["name"] == opportunity.name
        assert data["organization"] == opportunity.organization.slug
        assert data["currency"] == opportunity.currency_fk_id
        assert "learn_app" in data
        assert "deliver_app" in data
        assert "payment_units" in data
        assert "verification_flags" in data

    def test_to_representation_converts_ordered_dict(self):
        opportunity = OpportunityFactory()
        PaymentUnitFactory(opportunity=opportunity)

        serializer = OpportunitySerializer(opportunity)
        data = serializer.data

        # Verify nested data is converted to regular dicts
        assert isinstance(data, dict)
        assert isinstance(data["learn_app"], dict)
        assert isinstance(data["deliver_app"], dict)
        assert isinstance(data["payment_units"], list)
        if data["payment_units"]:
            assert isinstance(data["payment_units"][0], dict)