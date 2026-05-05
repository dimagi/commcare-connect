import logging

import httpx
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils.translation import gettext_lazy as _
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from commcare_connect.opportunity.app_xml import AppNoBuildException
from commcare_connect.opportunity.tasks import sync_learn_modules_and_deliver_units
from commcare_connect.organization.decorators import IsProgramManagerAdmin, user_is_org_admin
from commcare_connect.program.api.serializers import (
    ManagedOpportunityCreateSerializer,
    ManagedOpportunityResponseSerializer,
    ProgramApplicationCreateSerializer,
    ProgramApplicationResponseSerializer,
    ProgramCreateSerializer,
    ProgramResponseSerializer,
)
from commcare_connect.program.models import Program, ProgramApplication, ProgramApplicationStatus
from commcare_connect.program.tasks import send_opportunity_created_email, send_program_invite_email
from commcare_connect.utils.commcarehq_api import CommCareHQAPIException

logger = logging.getLogger(__name__)


class ProgramCreateView(APIView):
    permission_classes = [IsAuthenticated, IsProgramManagerAdmin]

    def post(self, request):
        serializer = ProgramCreateSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        program = serializer.save()
        return Response(ProgramResponseSerializer(program).data, status=status.HTTP_201_CREATED)


class ProgramAPIView(APIView):
    """Base view for endpoints scoped to a program — resolves program and checks caller is admin of its org."""

    permission_classes = [IsAuthenticated]

    def get_program(self):
        program = get_object_or_404(Program, program_id=self.kwargs["program_id"])
        if not user_is_org_admin(self.request.user, program.organization):
            self.permission_denied(self.request)
        return program


class ProgramApplicationCreateView(ProgramAPIView):
    def post(self, request, program_id):
        program = self.get_program()
        serializer = ProgramApplicationCreateSerializer(
            data=request.data, context={"request": request, "program": program}
        )
        serializer.is_valid(raise_exception=True)
        application = serializer.save()
        transaction.on_commit(lambda: send_program_invite_email(application.id))
        return Response(
            ProgramApplicationResponseSerializer(application).data,
            status=status.HTTP_201_CREATED,
        )


class ProgramApplicationAcceptView(ProgramAPIView):
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
        application.save(update_fields=["status", "modified_by", "date_modified"])
        return Response(ProgramApplicationResponseSerializer(application).data)


class ManagedOpportunityCreateView(ProgramAPIView):
    def post(self, request, program_id):
        program = self.get_program()
        serializer = ManagedOpportunityCreateSerializer(
            data=request.data, context={"request": request, "program": program}
        )
        serializer.is_valid(raise_exception=True)

        try:
            with transaction.atomic():
                opportunity = serializer.save()
                sync_learn_modules_and_deliver_units(opportunity)
                transaction.on_commit(lambda: send_opportunity_created_email(opportunity.id))
        except (
            CommCareHQAPIException,
            AppNoBuildException,
            httpx.RequestError,
            httpx.TimeoutException,
            httpx.ConnectError,
        ):
            logger.exception("Failed to fetch app metadata from HQ while creating managed opportunity")
            return Response(
                {"non_field_errors": [_("Failed to fetch app metadata from CommCare HQ.")]},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        return Response(
            ManagedOpportunityResponseSerializer(opportunity).data,
            status=status.HTTP_201_CREATED,
        )
