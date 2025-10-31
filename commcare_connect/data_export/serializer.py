from collections import OrderedDict

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from commcare_connect.opportunity.api.serializers import (
    CommCareAppSerializer,
    OpportunityClaimLimitSerializer,
    OpportunityVerificationFlagsSerializer,
    PaymentUnitSerializer,
)
from commcare_connect.opportunity.models import (
    Assessment,
    CompletedModule,
    CompletedWork,
    Opportunity,
    OpportunityClaimLimit,
    Payment,
    PaymentInvoice,
    UserVisit,
)
from commcare_connect.organization.models import Organization
from commcare_connect.program.models import Program


class OpportunityDataExportSerializer(serializers.ModelSerializer):
    organization = serializers.SlugRelatedField(read_only=True, slug_field="slug")
    program = serializers.SerializerMethodField()
    visit_count = serializers.SerializerMethodField()

    class Meta:
        model = Opportunity
        fields = ["id", "name", "date_created", "organization", "end_date", "is_active", "program", "visit_count"]

    def get_program(self, obj) -> int:
        return obj.managedopportunity.program_id if obj.managed else None

    def get_visit_count(self, obj) -> int:
        return obj.uservisit_set.count()


class OrganizationDataExportSerializer(serializers.ModelSerializer):
    class Meta:
        model = Organization
        fields = ["slug", "name"]


class ProgramDataExportSerializer(serializers.ModelSerializer):
    organization = serializers.SlugRelatedField(read_only=True, slug_field="slug")
    delivery_type = serializers.SlugRelatedField(read_only=True, slug_field="slug")

    class Meta:
        model = Program
        fields = ["id", "name", "delivery_type", "currency", "organization"]


class OpportunityUserDataSerializer(serializers.Serializer):
    username = serializers.CharField()
    phone = serializers.CharField()
    date_learn_started = serializers.DateTimeField()
    user_invite_status = serializers.CharField()
    payment_accrued = serializers.IntegerField()
    suspended = serializers.BooleanField()
    suspension_date = serializers.DateTimeField()
    suspension_reason = serializers.CharField()
    invited_date = serializers.DateTimeField()
    completed_learn_date = serializers.DateTimeField()
    last_active = serializers.DateTimeField()
    date_claimed = serializers.DateField()
    claim_limits = serializers.SerializerMethodField()

    @extend_schema_field(OpportunityClaimLimitSerializer.many_init())
    def get_claim_limits(self, obj):
        claim_limits = OpportunityClaimLimit.objects.filter(opportunity_claim__opportunity_access=obj)
        data = OpportunityClaimLimitSerializer(claim_limits, many=True).data
        return [dict(row) for row in data]


class UserVisitDataSerialier(serializers.ModelSerializer):
    username = serializers.SerializerMethodField()

    class Meta:
        model = UserVisit
        fields = [
            "opportunity_id",
            "username",
            "deliver_unit",
            "entity_id",
            "entity_name",
            "visit_date",
            "status",
            "reason",
            "location",
            "flagged",
            "flag_reason",
            "form_json",
            "completed_work",
            "status_modified_date",
            "review_status",
            "review_created_on",
            "justification",
            "date_created",
            "completed_work_id",
            "deliver_unit_id",
        ]

    def get_username(self, obj) -> str:
        return obj.username


class CompletedWorkDataSerializer(serializers.ModelSerializer):
    username = serializers.SerializerMethodField()
    opportunity_id = serializers.SerializerMethodField()

    class Meta:
        model = CompletedWork
        fields = [
            "username",
            "opportunity_id",
            "payment_unit_id",
            "status",
            "last_modified",
            "entity_id",
            "entity_name",
            "reason",
            "status_modified_date",
            "payment_date",
            "date_created",
            "saved_completed_count",
            "saved_approved_count",
            "saved_payment_accrued",
            "saved_payment_accrued_usd",
            "saved_org_payment_accrued",
            "saved_org_payment_accrued_usd",
        ]

    def get_username(self, obj) -> str:
        return obj.username

    def get_opportunity_id(self, obj) -> int:
        return obj.opportunity_id


class PaymentDataSerializer(serializers.ModelSerializer):
    username = serializers.SerializerMethodField()
    opportunity_id = serializers.SerializerMethodField()
    organization = serializers.SlugRelatedField(read_only=True, slug_field="slug")

    class Meta:
        model = Payment
        fields = [
            "username",
            "opportunity_id",
            "created_at",
            "amount",
            "amount_usd",
            "date_paid",
            "payment_unit",
            "confirmed",
            "confirmation_date",
            "organization",
            "invoice_id",
            "payment_method",
            "payment_operator",
        ]

    def get_username(self, obj) -> str:
        return obj.username

    def get_opportunity_id(self, obj) -> int:
        return obj.opportunity_id


class InvoiceDataSerializer(serializers.ModelSerializer):
    class Meta:
        model = PaymentInvoice
        fields = [
            "opportunity_id",
            "amount",
            "amount_usd",
            "date",
            "invoice_number",
            "service_delivery",
            "exchange_rate",
        ]


class AssessmentDataSerializer(serializers.ModelSerializer):
    username = serializers.SerializerMethodField()

    class Meta:
        model = Assessment
        fields = ["username", "app", "opportunity_id", "date", "score", "passing_score", "passed"]

    def get_username(self, obj) -> str:
        return obj.username


class CompletedModuleDataSerializer(serializers.ModelSerializer):
    username = serializers.SerializerMethodField()

    class Meta:
        model = CompletedModule
        fields = ["username", "module", "opportunity_id", "date", "duration"]

    def get_username(self, obj) -> str:
        return obj.username


class OpportunitySerializer(serializers.ModelSerializer):
    organization = serializers.SlugRelatedField(read_only=True, slug_field="slug")
    learn_app = CommCareAppSerializer()
    deliver_app = CommCareAppSerializer()
    payment_units = PaymentUnitSerializer(source="paymentunit_set", many=True)
    verification_flags = OpportunityVerificationFlagsSerializer(source="opportunityverificationflags", read_only=True)

    class Meta:
        model = Opportunity
        fields = [
            "id",
            "name",
            "description",
            "short_description",
            "date_created",
            "date_modified",
            "organization",
            "learn_app",
            "deliver_app",
            "start_date",
            "end_date",
            "max_visits_per_user",
            "daily_max_visits_per_user",
            "budget_per_user",
            "budget_per_visit",
            "total_budget",
            "currency",
            "is_active",
            "payment_units",
            "verification_flags",
        ]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        for key, value in data.items():
            if isinstance(value, OrderedDict):
                data[key] = dict(value)
            elif isinstance(value, list):
                cleaned_value = []
                for item in value:
                    if isinstance(item, OrderedDict):
                        cleaned_value.append(dict(item))
                data[key] = cleaned_value
        return data
