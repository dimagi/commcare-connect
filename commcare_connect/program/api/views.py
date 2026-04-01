from django.shortcuts import get_object_or_404
from oauth2_provider.contrib.rest_framework.permissions import TokenHasScope
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated

from commcare_connect.opportunity.api.permissions import IsOrgProgramManagerAdmin
from commcare_connect.opportunity.api.views import ReadSerializerResponseMixin
from commcare_connect.program.api.serializers import (
    ManagedOpportunityCreateSerializer,
    ManagedOpportunityReadSerializer,
    ProgramApplicationCreateSerializer,
    ProgramApplicationReadSerializer,
    ProgramCreateSerializer,
    ProgramReadSerializer,
)
from commcare_connect.program.models import ManagedOpportunity, Program, ProgramApplication


class ProgramViewSet(ReadSerializerResponseMixin, viewsets.ModelViewSet):
    serializer_class = ProgramReadSerializer
    read_serializer_class = ProgramReadSerializer
    permission_classes = [IsAuthenticated]
    required_scopes = ["create"]
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_permissions(self):
        if self.action in ("create", "partial_update"):
            return [IsAuthenticated(), TokenHasScope(), IsOrgProgramManagerAdmin()]
        return [IsAuthenticated()]

    def get_serializer_class(self):
        if self.action in ("create", "partial_update"):
            return ProgramCreateSerializer
        return ProgramReadSerializer

    def get_queryset(self):
        qs = Program.objects.filter(organization__memberships__user=self.request.user)
        org_slug = self.request.query_params.get("organization")
        if org_slug:
            qs = qs.filter(organization__slug=org_slug)
        return qs.distinct().order_by("-start_date")


class ProgramNestedViewMixin:
    """Shared logic for viewsets nested under a Program."""

    def initial(self, request, *args, **kwargs):
        """Inject PM org slug for permission checking on nested program routes."""
        if self.kwargs.get("program_id"):
            program = get_object_or_404(Program, program_id=self.kwargs["program_id"])
            self.kwargs["org_slug"] = program.organization.slug
        super().initial(request, *args, **kwargs)

    def get_program(self):
        return get_object_or_404(Program, program_id=self.kwargs["program_id"])

    def get_serializer_context(self):
        context = super().get_serializer_context()
        if self.kwargs.get("program_id"):
            context["program"] = self.get_program()
        return context


class ManagedOpportunityViewSet(ReadSerializerResponseMixin, ProgramNestedViewMixin, viewsets.ModelViewSet):
    serializer_class = ManagedOpportunityReadSerializer
    read_serializer_class = ManagedOpportunityReadSerializer
    permission_classes = [IsAuthenticated]
    required_scopes = ["create"]
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_permissions(self):
        if self.action in ("create", "partial_update"):
            return [IsAuthenticated(), TokenHasScope(), IsOrgProgramManagerAdmin()]
        return [IsAuthenticated()]

    def get_serializer_class(self):
        if self.action in ("create", "partial_update"):
            return ManagedOpportunityCreateSerializer
        return ManagedOpportunityReadSerializer

    def get_queryset(self):
        return ManagedOpportunity.objects.filter(
            program__program_id=self.kwargs["program_id"],
            program__organization__memberships__user=self.request.user,
        ).order_by("-date_created")


class ProgramApplicationViewSet(ReadSerializerResponseMixin, ProgramNestedViewMixin, viewsets.ModelViewSet):
    serializer_class = ProgramApplicationReadSerializer
    read_serializer_class = ProgramApplicationReadSerializer
    permission_classes = [IsAuthenticated]
    required_scopes = ["create"]
    http_method_names = ["get", "post", "head", "options"]

    def get_permissions(self):
        if self.action == "create":
            return [IsAuthenticated(), TokenHasScope(), IsOrgProgramManagerAdmin()]
        return [IsAuthenticated()]

    def get_serializer_class(self):
        if self.action == "create":
            return ProgramApplicationCreateSerializer
        return ProgramApplicationReadSerializer

    def get_queryset(self):
        return ProgramApplication.objects.filter(
            program__program_id=self.kwargs["program_id"],
            program__organization__memberships__user=self.request.user,
        ).order_by("-date_created")
