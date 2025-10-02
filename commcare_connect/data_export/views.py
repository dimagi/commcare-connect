import csv

from django.db.models import F
from django.http import JsonResponse, StreamingHttpResponse
from oauth2_provider.contrib.rest_framework.permissions import TokenHasScope
from rest_framework.generics import RetrieveAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from commcare_connect.data_export.serializer import (
    AssessmentDataSerializer,
    CompletedModuleDataSerializer,
    CompletedWorkDataSerializer,
    OpportunityDataExportSerializer,
    OpportunityUserDataSerializer,
    OrganizationDataExportSerializer,
    ProgramDataExportSerializer,
    UserVisitDataSerialier,
)
from commcare_connect.opportunity.api.serializers import BaseOpportunitySerializer
from commcare_connect.opportunity.models import (
    Assessment,
    CompletedModule,
    CompletedWork,
    Opportunity,
    OpportunityAccess,
    UserVisit,
)
from commcare_connect.organization.models import Organization
from commcare_connect.program.models import Program


class BaseDataExportView(APIView):
    permission_classes = [IsAuthenticated, TokenHasScope]
    required_scopes = ["export"]


class PseudoSupportsWrite:
    def write(self, value):
        return value


class BaseStreamingCSVExportView(BaseDataExportView):
    serializer_class = None

    def get_serializer_class(self, *args, **kwargs):
        return self.serializer_class

    def get_queryset(self, *args, **kwargs):
        raise NotImplementedError

    def get(self, *args, **kwargs):
        objects = self.get_queryset(*args, **kwargs)
        serialized_data = self.serializer_class(objects, many=True).data
        fieldnames = self.serializer_class().get_fields().keys()
        writer = csv.DictWriter(PseudoSupportsWrite(), fieldnames=fieldnames)
        data = [writer.writeheader()] + [writer.writerow(row) for row in serialized_data]
        return StreamingHttpResponse(data, content_type="text/csv")


class ProgramOpportunityOrganizationDataView(BaseDataExportView):
    def get(self):
        organizations = Organization.objects.filter(memberships__user=self.request.user)
        opportunities = Opportunity.objects.filter(organization__in=organizations)
        programs = Program.objects.filter(organization__in=organizations)

        org_data = OrganizationDataExportSerializer(organizations, many=True).data
        opp_data = OpportunityDataExportSerializer(opportunities, many=True).data
        program_data = ProgramDataExportSerializer(programs, many=True).data
        return JsonResponse({"organizations": org_data, "opportunities": opp_data, "programs": program_data})


class SingleOpportunityDataView(RetrieveAPIView, BaseDataExportView):
    serializer_class = BaseOpportunitySerializer

    def get_object(self):
        return Opportunity.objects.get(id=self.kwargs.get("opp_id"), organization__memberships__user=self.request.user)


class OpportunityUserDataView(BaseDataExportView):
    def get(self, request, opp_id):
        users = (
            OpportunityAccess.objects.filter(opportunity_id=opp_id)
            .annotate(
                username=F("user__username"),
                phone=F("user__phone_number"),
                user_invite_status=F("userinvite__status"),
                date_claimed=F("opportunityclaim__date_claimed"),
            )
            .values()
        )
        user_data = OpportunityUserDataSerializer(users, many=True).data
        return JsonResponse({"users": user_data})


class UserVisitDataView(BaseStreamingCSVExportView):
    serializer_class = UserVisitDataSerialier

    def get_queryset(self, request, opp_id):
        return (
            UserVisit.objects.filter(opportunity_id=opp_id)
            .annotate(username=F("user__username"))
            .select_related("user")
        )


class CompletedWorkDataView(BaseStreamingCSVExportView):
    serializer_class = CompletedWorkDataSerializer

    def get_queryset(self, request, opp_id):
        return (
            CompletedWork.objects.filter(opportunity_access__opportunity_id=opp_id)
            .annotate(
                username=F("opportunity_access__user__username"),
                opportunity_id=F("opportunity_access__opportunity_id"),
            )
            .select_related("opportunity_access")
        )


class CompletedModuleDataView(BaseStreamingCSVExportView):
    serializer_class = CompletedModuleDataSerializer

    def get_queryset(self, request, opp_id):
        return CompletedModule.objects.filter(opportunity_id=opp_id).annotate(
            username=F("opportunity_access__user__username"),
        )


class AssessmentDataView(BaseStreamingCSVExportView):
    serializer_class = AssessmentDataSerializer

    def get_queryset(self, request, opp_id):
        return Assessment.objects.filter(opportunity_id=opp_id).annotate(
            username=F("opportunity_access__user__username"),
        )


class OrganizationProgramDataView(BaseStreamingCSVExportView):
    serializer_class = ProgramDataExportSerializer

    def get_queryset(self, request, org_slug):
        return Program.objects.filter(organization__slug=org_slug)


class ProgramOpportunityDataView(BaseStreamingCSVExportView):
    serializer_class = OpportunityDataExportSerializer

    def get_queryset(self, request, program_id):
        return Opportunity.objects.filter(managedopportunity__program=program_id)
