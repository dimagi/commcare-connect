import csv
from collections import defaultdict

from django.core.files.storage import storages
from django.db.models import Count, F, Q
from django.http import FileResponse, JsonResponse, StreamingHttpResponse
from drf_spectacular.utils import extend_schema, inline_serializer
from oauth2_provider.contrib.rest_framework.permissions import TokenHasScope
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.generics import ListCreateAPIView, RetrieveAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from commcare_connect.data_export.const import (
    APP_TYPE_BOTH,
    APP_TYPE_DELIVER,
    APP_TYPE_LEARN,
    DELIVER_APP_KEY,
    LEARN_APP_KEY,
    VALID_APP_TYPES,
)
from commcare_connect.data_export.pagination import IdKeysetPagination
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
    UserVisitDataSerializer,
    UserVisitDataWithImagesSerializer,
)
from commcare_connect.opportunity.models import (
    Assessment,
    BlobMeta,
    CompletedModule,
    CompletedWork,
    LabsRecord,
    Opportunity,
    OpportunityAccess,
    Payment,
    PaymentInvoice,
    UserVisit,
)
from commcare_connect.organization.models import Organization
from commcare_connect.program.models import Program
from commcare_connect.users.models import User
from commcare_connect.utils.commcarehq_api import CommCareHQAPIException, get_app_structure

STREAM_CHUNK_SIZE = 2000


class BaseDataExportView(APIView):
    permission_classes = [IsAuthenticated, TokenHasScope]
    required_scopes = ["export"]


class OpportunityDataExportView(BaseDataExportView):
    def check_opportunity_permission(self, user, opp_id):
        self.opportunity = _get_opportunity_or_404(user, opp_id)

    def check_permissions(self, request):
        super().check_permissions(request)
        self.check_opportunity_permission(request.user, self.kwargs.get("opp_id"))


class EchoWriter:
    """A Buffer interface that implements write for csv.writer
    and returns back the value passed to write."""

    def write(self, value):
        return value


class BaseDataExportListView(BaseDataExportView):
    serializer_class = None
    pagination_class = IdKeysetPagination

    def get_serializer_class(self, *args, **kwargs):
        return self.serializer_class

    def get_queryset(self, *args, **kwargs):
        raise NotImplementedError

    def get_data_generator(self, *args, **kwargs):
        serializer_class = self.get_serializer_class()
        fieldnames = serializer_class().get_fields().keys()
        writer = csv.DictWriter(EchoWriter(), fieldnames=fieldnames)
        objects = self.get_queryset(*args, **kwargs).iterator(chunk_size=STREAM_CHUNK_SIZE)
        yield writer.writeheader()

        for obj in objects:
            serialized_data = serializer_class(obj).data
            yield writer.writerow(serialized_data)

    def paginate_queryset(self, queryset):
        self._paginator = self.pagination_class()
        return self._paginator.paginate_queryset(queryset, self.request)

    def get_paginated_response(self, data):
        return self._paginator.get_paginated_response(data)

    def post_paginate(self, page):
        """Hook called after pagination, before serialization. Override to modify the page list in-place.

        Note: this hook is only called for v2.0 requests (paginated JSON). It is not invoked
        for v1.0 requests, which use streaming CSV via ``get_data_generator``.
        """
        pass

    @extend_schema(
        description=(
            "v1.0: Returns CSV text StreamingHttpResponse. " "v2.0: Returns paginated JSON with 'next' and 'results'."
        )
    )
    def get(self, *args, **kwargs):
        if self.request.version == "2.0":
            queryset = self.get_queryset(*args, **kwargs)
            page = self.paginate_queryset(queryset)
            self.post_paginate(page)
            serializer_class = self.get_serializer_class()
            serializer = serializer_class(page, many=True)
            return self.get_paginated_response(serializer.data)
        return StreamingHttpResponse(self.get_data_generator(*args, **kwargs), content_type="text/csv")


def _get_opportunity_or_404(user, opp_id):
    try:
        return (
            Opportunity.objects.filter(
                Q(organization__memberships__user=user)
                | Q(managedopportunity__program__organization__memberships__user=user),
                id=opp_id,
            )
            .distinct()
            .get()
        )
    except Opportunity.DoesNotExist:
        raise NotFound()


def _get_program_or_404(user, program_id):
    try:
        return (
            Program.objects.filter(
                organization__memberships__user=user,
                id=program_id,
            )
            .distinct()
            .get()
        )
    except Program.DoesNotExist:
        raise NotFound()


def _get_org_or_404(user, org_id):
    try:
        return (
            Organization.objects.filter(
                memberships__user=user,
                id=org_id,
            )
            .distinct()
            .get()
        )
    except Organization.DoesNotExist:
        raise NotFound()


class ProgramOpportunityOrganizationDataView(BaseDataExportView):
    @extend_schema(
        responses=inline_serializer(
            "ProgramOpportunityOrganizationDataSerializer",
            {
                "organizations": OrganizationDataExportSerializer(),
                "opportunities": OpportunityDataExportSerializer(),
                "programs": ProgramDataExportSerializer(),
            },
        )
    )
    def get(self, request):
        organizations = Organization.objects.filter(memberships__user=request.user)
        opportunities = (
            Opportunity.objects.filter(
                Q(organization__in=organizations) | Q(managedopportunity__program__organization__in=organizations)
            )
            .annotate(visit_count=Count("uservisit", distinct=True))
            .distinct()
        )
        programs = Program.objects.filter(organization__in=organizations)

        org_data = OrganizationDataExportSerializer(organizations, many=True).data
        opp_data = OpportunityDataExportSerializer(opportunities, many=True).data
        program_data = ProgramDataExportSerializer(programs, many=True).data
        return JsonResponse({"organizations": org_data, "opportunities": opp_data, "programs": program_data})


class SingleOpportunityDataView(RetrieveAPIView, BaseDataExportView):
    serializer_class = OpportunitySerializer

    def get_object(self):
        return _get_opportunity_or_404(self.request.user, self.kwargs.get("opp_id"))


class OpportunityScopedDataView(OpportunityDataExportView, BaseDataExportListView):
    pass


class OpportunityUserDataView(OpportunityScopedDataView):
    serializer_class = OpportunityUserDataSerializer

    def get_queryset(self, request, opp_id):
        return OpportunityAccess.objects.filter(opportunity=self.opportunity).annotate(
            username=F("user__username"),
            name=F("user__name"),
            phone=F("user__phone_number"),
            user_invite_status=F("userinvite__status"),
            date_claimed=F("opportunityclaim__date_claimed"),
        )


class UserVisitDataView(OpportunityScopedDataView):
    serializer_class = UserVisitDataSerializer

    def _include_images(self):
        return self.request.query_params.get("images", "").lower() == "true"

    def get_serializer_class(self, *args, **kwargs):
        if self._include_images():
            return UserVisitDataWithImagesSerializer
        return UserVisitDataSerializer

    def get_queryset(self, request, opp_id):
        return (
            UserVisit.objects.filter(opportunity=self.opportunity)
            .annotate(username=F("user__username"))
            .select_related("user")
        )

    def post_paginate(self, page):
        if self._include_images():
            self._prefetch_images(page)

    def get_data_generator(self, *args, **kwargs):
        serializer_class = self.get_serializer_class()
        fieldnames = serializer_class().get_fields().keys()
        writer = csv.DictWriter(EchoWriter(), fieldnames=fieldnames)
        yield writer.writeheader()

        queryset = self.get_queryset(*args, **kwargs)
        include_images = self._include_images()

        if not include_images:
            for obj in queryset.iterator(chunk_size=STREAM_CHUNK_SIZE):
                yield writer.writerow(serializer_class(obj).data)
        else:
            batch = []
            for obj in queryset.iterator(chunk_size=STREAM_CHUNK_SIZE):
                batch.append(obj)
                if len(batch) >= STREAM_CHUNK_SIZE:
                    self._prefetch_images(batch)
                    for visit in batch:
                        yield writer.writerow(serializer_class(visit).data)
                    batch = []
            if batch:
                self._prefetch_images(batch)
                for visit in batch:
                    yield writer.writerow(serializer_class(visit).data)

    def _prefetch_images(self, visits):
        xform_ids = [v.xform_id for v in visits]
        blobs_by_parent = defaultdict(list)
        for blob in BlobMeta.objects.filter(parent_id__in=xform_ids, content_type__startswith="image/"):
            blobs_by_parent[blob.parent_id].append(blob)
        for visit in visits:
            visit._prefetched_images = blobs_by_parent.get(visit.xform_id, [])


class CompletedWorkDataView(OpportunityScopedDataView):
    serializer_class = CompletedWorkDataSerializer

    def get_queryset(self, request, opp_id):
        return (
            CompletedWork.objects.filter(opportunity_access__opportunity=self.opportunity)
            .annotate(
                username=F("opportunity_access__user__username"),
                opportunity_id=F("opportunity_access__opportunity_id"),
            )
            .select_related("opportunity_access")
        )


class PaymentDataView(OpportunityScopedDataView):
    serializer_class = PaymentDataSerializer

    def get_queryset(self, request, opp_id):
        return Payment.objects.filter(
            Q(opportunity_access__opportunity=self.opportunity) | Q(invoice__opportunity=self.opportunity)
        ).annotate(
            username=F("opportunity_access__user__username"),
            opportunity_id=F("opportunity_access__opportunity_id"),
        )


class InvoiceDataView(OpportunityScopedDataView):
    serializer_class = InvoiceDataSerializer

    def get_queryset(self, request, opp_id):
        opportunity = _get_opportunity_or_404(request.user, opp_id)
        return PaymentInvoice.objects.filter(opportunity=opportunity)


class CompletedModuleDataView(OpportunityScopedDataView):
    serializer_class = CompletedModuleDataSerializer

    def get_queryset(self, request, opp_id):
        queryset = CompletedModule.objects.filter(opportunity=self.opportunity)
        username = request.query_params.get("username")
        if username:
            queryset = queryset.filter(opportunity_access__user__username=username)
        queryset = queryset.annotate(
            username=F("opportunity_access__user__username"),
        )
        return queryset


class AssessmentDataView(OpportunityScopedDataView):
    serializer_class = AssessmentDataSerializer

    def get_queryset(self, request, opp_id):
        return Assessment.objects.filter(opportunity=self.opportunity).annotate(
            username=F("opportunity_access__user__username"),
        )


class LabsRecordDataView(BaseDataExportView, ListCreateAPIView):
    serializer_class = LabsRecordDataSerializer

    def _check_get_permissions(self, request):
        params = request.query_params
        if params.get("opportunity_id"):
            opp_id = params.get("opportunity_id")
            self.opportunity = _get_opportunity_or_404(request.user, opp_id)
        elif params.get("program_id"):
            program_id = params.get("program_id")
            self.program = _get_program_or_404(request.user, program_id)
        elif params.get("organization_id"):
            org_id = params.get("organization_id")
            self.organization = _get_org_or_404(request.user, org_id)
        else:
            self.public = True

    def _check_edit_permissions(self, request):
        data = request.data
        many = isinstance(data, list)
        if not many:
            data = [data]
        self.data = data
        opps = set()
        orgs = set()
        programs = set()
        for item in self.data:
            if item.get("opportunity_id"):
                opps.add(item["opportunity_id"])
            if item.get("program_id"):
                programs.add(item["program_id"])
            if item.get("organization_id"):
                orgs.add(item["organization_id"])
        for opp_id in opps:
            _get_opportunity_or_404(request.user, opp_id)
        for program_id in programs:
            _get_program_or_404(request.user, program_id)
        for org_id in orgs:
            _get_org_or_404(request.user, org_id)

    def check_permissions(self, request):
        super().check_permissions(request)
        if request.method == "GET":
            self._check_get_permissions(request)
        elif request.method in ["POST", "DELETE"]:
            self._check_edit_permissions(request)

    def get_queryset(self):
        filters = {}
        query_params = self.request.query_params.copy()
        for key, value in query_params.items():
            filters[key] = value
        queryset = LabsRecord.objects.filter(**filters)
        if hasattr(self, "public"):
            queryset = queryset.filter(public=self.public)
        if hasattr(self, "opportunity"):
            queryset = queryset.filter(opportunity=self.opportunity)
        if hasattr(self, "program"):
            queryset = queryset.filter(program=self.program)
        if hasattr(self, "organization"):
            queryset = queryset.filter(organization=self.organization)
        queryset = queryset.annotate(
            username=F("user__username"),
        )
        return queryset

    def create(self, request, *args, **kwargs):
        # Handles upsert (update or create) for LabsRecord via JSON data

        instances = []
        for item in self.data:
            item = item.copy()
            username = item.pop("username", None)
            user = None
            if username:
                user = User.objects.get(username=username)
                item["user"] = user
            pk = item.pop("id", None)
            obj, created = LabsRecord.objects.update_or_create(defaults=item, **{"id": pk})
            instances.append(obj)
        serializer = self.get_serializer(instances, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def delete(self, request, *args, **kwargs):
        ids = [item["id"] for item in self.data]
        LabsRecord.objects.filter(pk__in=ids).delete()
        return Response(status=status.HTTP_200_OK)


class ImageView(OpportunityDataExportView):
    def get(self, request, *args, **kwargs):
        blob_id = request.query_params["blob_id"]
        blob_meta = BlobMeta.objects.get(blob_id=blob_id)
        form = UserVisit.objects.get(xform_id=blob_meta.parent_id)
        _get_opportunity_or_404(request.user, form.opportunity_id)
        attachment = storages["default"].open(blob_id)
        return FileResponse(attachment, filename=blob_meta.name, content_type=blob_meta.content_type)


class AppStructureView(OpportunityDataExportView):
    def get(self, request, opp_id):
        app_type = request.query_params.get("app_type", APP_TYPE_BOTH)
        if app_type not in VALID_APP_TYPES:
            return Response(
                {"error": f"Invalid app_type. Must be one of: {', '.join(VALID_APP_TYPES)}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not self.opportunity.api_key:
            raise NotFound("Opportunity does not have an associated API key.")

        result = {LEARN_APP_KEY: None, DELIVER_APP_KEY: None}

        try:
            if app_type in (APP_TYPE_LEARN, APP_TYPE_BOTH) and self.opportunity.learn_app:
                result[LEARN_APP_KEY] = get_app_structure(self.opportunity.api_key, self.opportunity.learn_app)

            if app_type in (APP_TYPE_DELIVER, APP_TYPE_BOTH) and self.opportunity.deliver_app:
                result[DELIVER_APP_KEY] = get_app_structure(self.opportunity.api_key, self.opportunity.deliver_app)
        except CommCareHQAPIException:
            return Response(
                {"error": "Failed to fetch app structure from CommCare HQ."},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        return Response(result)


class OrganizationProgramDataView(BaseDataExportListView):
    serializer_class = ProgramDataExportSerializer

    def get_queryset(self, request, org_slug):
        return Program.objects.filter(organization__slug=org_slug, organization__memberships__user=self.request.user)


class ProgramOpportunityDataView(BaseDataExportListView):
    serializer_class = OpportunitySerializer

    def get_queryset(self, request, program_id):
        return (
            Opportunity.objects.filter(
                managedopportunity__program=program_id,
                managedopportunity__program__organization__memberships__user=self.request.user,
            )
            .select_related("learn_app", "deliver_app")
            .prefetch_related("paymentunit_set", "opportunityverificationflags")
        )
