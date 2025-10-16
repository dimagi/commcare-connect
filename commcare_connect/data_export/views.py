import csv

from django.db.models import F, Q
from django.http import JsonResponse, StreamingHttpResponse
from drf_spectacular.utils import extend_schema, inline_serializer
from oauth2_provider.contrib.rest_framework.permissions import TokenHasScope
from rest_framework.exceptions import NotFound
from rest_framework.generics import RetrieveAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from commcare_connect.data_export.serializer import (
    AssessmentDataSerializer,
    CompletedModuleDataSerializer,
    CompletedWorkDataSerializer,
    InvoiceDataSerializer,
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
    Opportunity,
    OpportunityAccess,
    Payment,
    PaymentInvoice,
    UserVisit,
)
from commcare_connect.organization.models import Organization
from commcare_connect.program.models import Program


class BaseDataExportView(APIView):
    permission_classes = [IsAuthenticated, TokenHasScope]
    required_scopes = ["export"]


class EchoWriter:
    """A Buffer interface that implements write for csv.writer
    and returns back the value passed to write."""

    def write(self, value):
        return value


class BaseStreamingCSVExportView(BaseDataExportView):
    serializer_class = None

    def get_serializer_class(self, *args, **kwargs):
        return self.serializer_class

    def get_queryset(self, *args, **kwargs):
        raise NotImplementedError

    def get_data_generator(self, *args, **kwargs):
        fieldnames = self.serializer_class().get_fields().keys()
        writer = csv.DictWriter(EchoWriter(), fieldnames=fieldnames)
        objects = self.get_queryset(*args, **kwargs)
        yield writer.writeheader()

        for obj in objects:
            serialized_data = self.serializer_class(obj).data
            yield writer.writerow(serialized_data)

    @extend_schema(
        description=(
            "This API returns a CSV text StreamingHttpResponse. "
            "The values shown in the example will be in CSV text format."
        )
    )
    def get(self, *args, **kwargs):
        return StreamingHttpResponse(self.get_data_generator(*args, **kwargs), content_type="text/csv")


def _get_opportunity_or_404(user, opp_id):
    opportunity = Opportunity.objects.filter(
        Q(organization__memberships__user=user) | Q(managedopportunity__program__organization__memberships__user=user),
        id=opp_id,
    ).first()

    if opportunity:
        return opportunity
    else:
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
        opportunities = Opportunity.objects.filter(organization__in=organizations)
        programs = Program.objects.filter(organization__in=organizations)

        org_data = OrganizationDataExportSerializer(organizations, many=True).data
        opp_data = OpportunityDataExportSerializer(opportunities, many=True).data
        program_data = ProgramDataExportSerializer(programs, many=True).data
        return JsonResponse({"organizations": org_data, "opportunities": opp_data, "programs": program_data})


class SingleOpportunityDataView(RetrieveAPIView, BaseDataExportView):
    serializer_class = OpportunitySerializer

    def get_object(self):
        return _get_opportunity_or_404(self.request.user, self.kwargs.get("opp_id"))


class OpportunityUserDataView(BaseStreamingCSVExportView):
    serializer_class = OpportunityUserDataSerializer

    def get_queryset(self, request, opp_id):
        opportunity = _get_opportunity_or_404(request.user, opp_id)
        return OpportunityAccess.objects.filter(opportunity=opportunity).annotate(
            username=F("user__username"),
            phone=F("user__phone_number"),
            user_invite_status=F("userinvite__status"),
            date_claimed=F("opportunityclaim__date_claimed"),
        )


class UserVisitDataView(BaseStreamingCSVExportView):
    serializer_class = UserVisitDataSerialier

    def get_queryset(self, request, opp_id):
        opportunity = _get_opportunity_or_404(request.user, opp_id)
        return (
            UserVisit.objects.filter(opportunity=opportunity)
            .annotate(username=F("user__username"))
            .select_related("user")
        )


class CompletedWorkDataView(BaseStreamingCSVExportView):
    serializer_class = CompletedWorkDataSerializer

    def get_queryset(self, request, opp_id):
        opportunity = _get_opportunity_or_404(request.user, opp_id)
        return (
            CompletedWork.objects.filter(opportunity_access__opportunity=opportunity)
            .annotate(
                username=F("opportunity_access__user__username"),
                opportunity_id=F("opportunity_access__opportunity_id"),
            )
            .select_related("opportunity_access")
        )


class PaymentDataView(BaseStreamingCSVExportView):
    serializer_class = PaymentDataSerializer

    def get_queryset(self, request, opp_id):
        opportunity = _get_opportunity_or_404(request.user, opp_id)
        return Payment.objects.filter(
            Q(opportunity_access__opportunity=opportunity) | Q(invoice__opportunity=opportunity)
        ).annotate(
            username=F("opportunity_access__user__username"),
            opportunity_id=F("opportunity_access__opportunity_id"),
        )


class InvoiceDataView(BaseStreamingCSVExportView):
    serializer_class = InvoiceDataSerializer

    def get_queryset(self, request, opp_id):
        opportunity = _get_opportunity_or_404(request.user, opp_id)
        return PaymentInvoice.objects.filter(opportunity=opportunity)


class CompletedModuleDataView(BaseStreamingCSVExportView):
    serializer_class = CompletedModuleDataSerializer

    def get_queryset(self, request, opp_id):
        opportunity = _get_opportunity_or_404(request.user, opp_id)
        return CompletedModule.objects.filter(opportunity=opportunity).annotate(
            username=F("opportunity_access__user__username"),
        )


class AssessmentDataView(BaseStreamingCSVExportView):
    serializer_class = AssessmentDataSerializer

    def get_queryset(self, request, opp_id):
        opportunity = _get_opportunity_or_404(request.user, opp_id)
        return Assessment.objects.filter(opportunity=opportunity).annotate(
            username=F("opportunity_access__user__username"),
        )


class OrganizationProgramDataView(BaseStreamingCSVExportView):
    serializer_class = ProgramDataExportSerializer

    def get_queryset(self, request, org_slug):
        return Program.objects.filter(organization__slug=org_slug, organization__memberships__user=self.request.user)


class ProgramOpportunityDataView(BaseStreamingCSVExportView):
    serializer_class = OpportunitySerializer

    def get_queryset(self, request, program_id):
        return (
            Opportunity.objects.filter(
                managedopportunity__program=program_id,
                managed_opportunity__program__organization__memberships__user=self.request.user,
            )
            .select_related("learn_app", "deliver_app")
            .prefetch_related("paymentunit_set", "opportunityverificationflags")
        )
