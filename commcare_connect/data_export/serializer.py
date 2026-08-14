from collections import OrderedDict

from django.contrib.gis.geos import GEOSException, GEOSGeometry
from django.db import IntegrityError, transaction
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from commcare_connect.audit.models import AuditReport, AuditReportEntry
from commcare_connect.commcarehq.api import bulk_create_or_update_cases_by_work_areas
from commcare_connect.microplanning.helpers import (
    assign_work_areas_and_sync_to_hq,
    unassign_work_areas_for_opportunity,
)
from commcare_connect.microplanning.models import SRID, WorkArea, WorkAreaGroup, WorkAreaStatus
from commcare_connect.microplanning.tasks import parse_lon_lat_centroid, send_work_area_assignment_notification
from commcare_connect.opportunity.api.serializers.mobile import (
    CommCareAppSerializer,
    OpportunityClaimLimitSerializer,
    OpportunityVerificationFlagsSerializer,
    PaymentUnitSerializer,
)
from commcare_connect.opportunity.models import (
    Assessment,
    AssignedTask,
    CompletedModule,
    CompletedWork,
    LabsRecord,
    Opportunity,
    OpportunityAccess,
    OpportunityClaimLimit,
    Payment,
    PaymentInvoice,
    TaskType,
    UserVisit,
)
from commcare_connect.organization.models import LLOEntity, Organization
from commcare_connect.program.models import Program


class LonLatPointField(serializers.Field):
    default_error_messages = {"invalid": "Centroid must be in 'lon lat' format."}

    def to_internal_value(self, data):
        try:
            return parse_lon_lat_centroid(data)
        except (GEOSException, ValueError, TypeError, AttributeError):
            self.fail("invalid")

    def to_representation(self, value):
        return f"{value.x} {value.y}"


class WKTPolygonField(serializers.Field):
    default_error_messages = {
        "invalid": "Invalid WKT.",
        "wrong_type": "Expected a WKT Polygon.",
    }

    def to_internal_value(self, data):
        try:
            geom = GEOSGeometry(data, srid=SRID)
        except (GEOSException, ValueError, TypeError):
            self.fail("invalid")

        if geom.geom_type != "Polygon":
            self.fail("wrong_type")

        return geom

    def to_representation(self, value):
        return value.wkt


class OpportunityDataExportSerializer(serializers.ModelSerializer):
    organization = serializers.SlugRelatedField(read_only=True, slug_field="slug")
    supervising_organization = serializers.SlugRelatedField(read_only=True, slug_field="slug")
    program = serializers.SerializerMethodField()
    visit_count = serializers.SerializerMethodField()

    class Meta:
        model = Opportunity
        fields = [
            "id",
            "name",
            "date_created",
            "organization",
            "supervising_organization",
            "end_date",
            "is_active",
            "program",
            "visit_count",
        ]

    def get_program(self, obj) -> int:
        return obj.program_id

    def get_visit_count(self, obj) -> int:
        return getattr(obj, "visit_count", 0)


class OrganizationDataExportSerializer(serializers.ModelSerializer):
    class Meta:
        model = Organization
        fields = ["id", "slug", "name", "funder"]


class ProgramDataExportSerializer(serializers.ModelSerializer):
    organization = serializers.SlugRelatedField(read_only=True, slug_field="slug")
    funder = serializers.SlugRelatedField(read_only=True, slug_field="slug")
    watchers = serializers.SlugRelatedField(read_only=True, slug_field="slug", many=True)
    delivery_type = serializers.SlugRelatedField(read_only=True, slug_field="slug")
    currency = serializers.CharField(source="currency_id", read_only=True)

    class Meta:
        model = Program
        fields = ["id", "name", "delivery_type", "currency", "organization", "funder", "watchers"]


class OpportunityUserDataSerializer(serializers.Serializer):
    username = serializers.CharField()
    name = serializers.CharField()
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


class UserVisitDataSerializer(serializers.ModelSerializer):
    username = serializers.SerializerMethodField()
    user_id = serializers.UUIDField(source="user.user_id")

    class Meta:
        model = UserVisit
        fields = [
            "id",
            "user_visit_id",
            "opportunity_id",
            "username",
            "user_id",
            "user_visit_id",
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


class UserVisitDataWithImagesSerializer(UserVisitDataSerializer):
    images = serializers.SerializerMethodField()

    class Meta(UserVisitDataSerializer.Meta):
        fields = UserVisitDataSerializer.Meta.fields + ["images"]

    def get_images(self, obj):
        blobs = getattr(obj, "_prefetched_images", None)
        if blobs is None:
            blobs = obj.images
        return [{"blob_id": b.blob_id, "name": b.name, "parent_id": b.parent_id} for b in blobs]


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


class LabsRecordDataSerializer(serializers.ModelSerializer):
    username = serializers.SerializerMethodField()

    class Meta:
        model = LabsRecord
        fields = [
            "id",
            "username",
            "experiment",
            "opportunity_id",
            "organization_id",
            "program_id",
            "labs_record_id",
            "type",
            "data",
            "public",
        ]

    def get_username(self, obj) -> str:
        return obj.user.username if obj.user else None


class CompletedModuleDataSerializer(serializers.ModelSerializer):
    username = serializers.SerializerMethodField()

    class Meta:
        model = CompletedModule
        fields = ["username", "module", "opportunity_id", "date", "duration"]

    def get_username(self, obj) -> str:
        return obj.username


class OpportunitySerializer(serializers.ModelSerializer):
    organization = serializers.SlugRelatedField(read_only=True, slug_field="slug")
    currency = serializers.CharField(source="currency_id", read_only=True)
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


class TaskTypeDataSerializer(serializers.ModelSerializer):
    class Meta:
        model = TaskType
        fields = [
            "id",
            "task_type_id",
            "name",
            "slug",
            "description",
            "unit_name",
            "case_property",
            "is_active",
            "archived",
            "duration",
        ]


class AssignedTaskDataSerializer(serializers.ModelSerializer):
    task_type_name = serializers.CharField(source="task_type.name", read_only=True)
    username = serializers.CharField(source="opportunity_access.user.username", read_only=True)

    class Meta:
        model = AssignedTask
        fields = [
            "id",
            "assigned_task_id",
            "task_type",
            "task_type_name",
            "username",
            "completed_at",
            "duration",
            "status",
            "due_date",
            "date_created",
        ]


class WorkAreaGroupDataSerializer(serializers.ModelSerializer):
    class Meta:
        model = WorkAreaGroup
        fields = ["id", "name", "ward", "opportunity"]


class WorkAreaDataSerializer(serializers.ModelSerializer):
    work_area_group_name = serializers.SerializerMethodField()
    centroid = serializers.SerializerMethodField()
    boundary = serializers.SerializerMethodField()

    class Meta:
        model = WorkArea
        fields = [
            "id",
            "slug",
            "ward",
            "status",
            "building_count",
            "expected_visit_count",
            "case_id",
            "case_properties",
            "work_area_group",
            "work_area_group_name",
            "centroid",
            "boundary",
        ]

    def get_work_area_group_name(self, obj) -> str | None:
        if obj.work_area_group_id is None:
            return None
        return obj.work_area_group.name

    def get_centroid(self, obj) -> dict:
        return {"type": "Point", "coordinates": [obj.centroid.x, obj.centroid.y]}

    def get_boundary(self, obj) -> dict:
        return {"type": "Polygon", "coordinates": obj.boundary.coords}


class LLOEntityDataSerializer(serializers.ModelSerializer):
    class Meta:
        model = LLOEntity
        fields = ["id", "name", "short_name"]


class AuditReportDataSerializer(serializers.ModelSerializer):
    completed_by_username = serializers.CharField(source="completed_by.username", read_only=True, default=None)

    class Meta:
        model = AuditReport
        fields = [
            "id",
            "audit_report_id",
            "opportunity",
            "period_start",
            "period_end",
            "status",
            "completed_by_username",
            "completed_date",
            "date_created",
            "date_modified",
        ]


class AuditReportEntryDataSerializer(serializers.ModelSerializer):
    audit_report_uuid = serializers.UUIDField(source="audit_report.audit_report_id", read_only=True)
    username = serializers.CharField(source="opportunity_access.user.username", read_only=True)

    class Meta:
        model = AuditReportEntry
        fields = [
            "id",
            "audit_report_entry_id",
            "audit_report",
            "audit_report_uuid",
            "opportunity_access",
            "username",
            "results",
            "flagged",
            "reviewed",
            "review_action",
            "date_created",
            "date_modified",
        ]


class WorkAreaGroupWriteSerializer(serializers.ModelSerializer):
    # Derived from member WorkAreas' boundaries via update_centroid() — never client-writable,
    # exposed here read-only so the response reflects the current computed value.
    centroid = LonLatPointField(read_only=True)

    class Meta:
        model = WorkAreaGroup
        fields = ["name", "ward", "centroid"]

    def validate_name(self, value):
        opportunity = self.context["view"].opportunity
        qs = WorkAreaGroup.objects.filter(opportunity=opportunity, name=value)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError(_("A work area group with this name already exists."))
        return value

    def create(self, validated_data):
        try:
            return WorkAreaGroup.objects.create(opportunity=self.context["view"].opportunity, **validated_data)
        except IntegrityError:
            raise serializers.ValidationError({"name": _("A work area group with this name already exists.")})


class WorkAreaBulkUpdateListSerializer(serializers.ListSerializer):
    def create(self, validated_data):
        opportunity = self.context["view"].opportunity

        instances = []
        touched_fields = set()
        needs_visit_status_update = []
        needs_hq_resync = []
        to_assign = []
        original_access_status = {}
        to_unassign = []
        recompute_group_ids = set()

        for item in validated_data:
            item = item.copy()
            instance = item.pop("id")
            has_access_key = "opportunity_access" in item
            access = item.pop("opportunity_access", None)
            old_group_id = instance.work_area_group_id

            for field, value in item.items():
                setattr(instance, field, value)
                touched_fields.add(field)

            if "expected_visit_count" in item:
                needs_visit_status_update.append(instance)

            if "boundary" in item and instance.work_area_group_id is not None:
                # Group centroid is derived from member WorkAreas' boundaries, so an edit here
                # needs a recompute even without a group change.
                recompute_group_ids.add(instance.work_area_group_id)

            if instance.work_area_group_id != old_group_id:
                recompute_group_ids.update({instance.work_area_group_id, old_group_id})

            if access is not None:
                # assign_work_areas_and_sync_to_hq expects opportunity_access/status already set.
                original_access_status[instance.id] = (instance.opportunity_access_id, instance.status)
                instance.opportunity_access = access
                if instance.status == WorkAreaStatus.UNASSIGNED:
                    instance.status = WorkAreaStatus.NOT_VISITED
                to_assign.append(instance)
            elif has_access_key:
                # Explicit `opportunity_access: null` — unassign rather than silently ignore.
                to_unassign.append(instance)
            elif item and instance.opportunity_access_id:
                needs_hq_resync.append(instance)

            instances.append(instance)

        if touched_fields:
            # Must run before the group-centroid recompute below: update_centroid() re-reads
            # member WorkAreas' boundaries from the DB, so it needs this write already committed.
            WorkArea.objects.bulk_update(instances, fields=list(touched_fields))

        for instance in needs_visit_status_update:
            instance.update_status()

        recompute_group_ids.discard(None)
        if recompute_group_ids:
            groups = list(WorkAreaGroup.objects.filter(pk__in=recompute_group_ids))
            for group in groups:
                group.update_centroid(commit=False)
            WorkAreaGroup.objects.bulk_update(groups, ["centroid"])

        if needs_hq_resync:
            bulk_create_or_update_cases_by_work_areas(needs_hq_resync, opportunity)

        if to_assign:
            result = assign_work_areas_and_sync_to_hq(opportunity, to_assign, self.context["request"].user)
            # Side-channeled on the serializer instance: ListSerializer.save() only returns
            # instances, so this is how the view recovers failed_ids/skipped counts.
            self.assign_result = result
            failed_ids = set(result["failed_ids"])
            notified_access_ids = set()
            for wa in to_assign:
                if wa.id in failed_ids:
                    orig_access_id, orig_status = original_access_status[wa.id]
                    wa.opportunity_access_id = orig_access_id
                    wa.status = orig_status
                else:
                    notified_access_ids.add(wa.opportunity_access_id)
            for access_id in notified_access_ids:
                transaction.on_commit(lambda aid=access_id: send_work_area_assignment_notification.delay(aid))

        if to_unassign:
            result = unassign_work_areas_for_opportunity(
                opportunity, [wa.id for wa in to_unassign], self.context["request"].user
            )
            # See assign_result above: same side-channel, for the unassign path.
            self.unassign_result = result
            unassigned_ids = set(result["unassigned_ids"])
            for wa in to_unassign:
                if wa.id in unassigned_ids:
                    wa.opportunity_access = None
                    wa.status = WorkAreaStatus.UNASSIGNED

        return instances


class WorkAreaBulkUpdateSerializer(serializers.ModelSerializer):
    id = serializers.PrimaryKeyRelatedField(queryset=WorkArea.objects.none())
    centroid = LonLatPointField(required=False)
    boundary = WKTPolygonField(required=False)
    work_area_group = serializers.PrimaryKeyRelatedField(
        queryset=WorkAreaGroup.objects.none(),
        required=False,
        allow_null=True,
    )
    opportunity_access = serializers.PrimaryKeyRelatedField(
        queryset=OpportunityAccess.objects.none(),
        required=False,
        allow_null=True,
    )

    class Meta:
        model = WorkArea
        list_serializer_class = WorkAreaBulkUpdateListSerializer
        fields = [
            "id",
            "work_area_group",
            "opportunity_access",
            "expected_visit_count",
            "target_population",
            "centroid",
            "boundary",
            "case_properties",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        opportunity = self.context["view"].opportunity

        self.fields["id"].queryset = WorkArea.objects.filter(opportunity=opportunity).select_related("work_area_group")
        self.fields["work_area_group"].queryset = WorkAreaGroup.objects.filter(opportunity=opportunity)
        self.fields["opportunity_access"].queryset = OpportunityAccess.objects.filter(opportunity=opportunity)
