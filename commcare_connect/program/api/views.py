from django.shortcuts import get_object_or_404
from django.utils.translation import gettext_lazy as _
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from commcare_connect.organization.decorators import IsProgramManagerAdmin, user_is_org_admin
from commcare_connect.program.api.serializers import (
    ProgramApplicationCreateSerializer,
    ProgramApplicationResponseSerializer,
    ProgramCreateSerializer,
    ProgramResponseSerializer,
)
from commcare_connect.program.models import Program, ProgramApplication, ProgramApplicationStatus


class ProgramCreateView(APIView):
    permission_classes = [IsAuthenticated, IsProgramManagerAdmin]

    def post(self, request):
        serializer = ProgramCreateSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        program = serializer.save()
        return Response(ProgramResponseSerializer(program).data, status=status.HTTP_201_CREATED)


class ProgramMixin:
    """Shared logic for views scoped to a program — resolves program and checks caller is admin of its org."""

    def get_program(self):
        program = get_object_or_404(Program, program_id=self.kwargs["program_id"])
        if not user_is_org_admin(self.request.user, program.organization):
            self.permission_denied(self.request)
        return program


class ProgramApplicationCreateView(ProgramMixin, APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, program_id):
        program = self.get_program()
        serializer = ProgramApplicationCreateSerializer(
            data=request.data, context={"request": request, "program": program}
        )
        serializer.is_valid(raise_exception=True)
        application = serializer.save()
        return Response(
            ProgramApplicationResponseSerializer(application).data,
            status=status.HTTP_201_CREATED,
        )


class ProgramApplicationAcceptView(ProgramMixin, APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, program_id, application_id):
        program = self.get_program()
        application = get_object_or_404(
            ProgramApplication,
            program=program,
            program_application_id=application_id,
        )
        if application.status not in (
            ProgramApplicationStatus.INVITED,
            ProgramApplicationStatus.APPLIED,
        ):
            return Response(
                {"status": [_("Cannot accept application with status '{status}'.").format(status=application.status)]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        application.status = ProgramApplicationStatus.ACCEPTED
        application.modified_by = request.user.email
        application.save(update_fields=["status", "modified_by"])
        return Response(ProgramApplicationResponseSerializer(application).data)
