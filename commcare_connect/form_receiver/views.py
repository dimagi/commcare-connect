import logging

from django.db import IntegrityError
from oauth2_provider.contrib.rest_framework import OAuth2Authentication, TokenHasReadWriteScope
from rest_framework import parsers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from commcare_connect.commcarehq.models import HQServer
from commcare_connect.form_receiver.processor import process_xform
from commcare_connect.form_receiver.serializers import XFormSerializer

logger = logging.getLogger(__name__)


class FormReceiver(APIView):
    parser_classes = [parsers.JSONParser]
    authentication_classes = [OAuth2Authentication]
    permission_classes = [TokenHasReadWriteScope]

    def get(self, request):
        """
        Simple GET endpoint to test connectivity and OAuth authentication.
        """
        return Response({"detail": "Connection successful"}, status=status.HTTP_200_OK)

    def post(self, request):
        serializer = XFormSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        xform = serializer.save()
        try:
            hq_server = HQServer.objects.get(oauth_application=request.auth.application)
        except HQServer.DoesNotExist as e:
            from commcare_connect.form_receiver.exceptions import ProcessingError

            raise ProcessingError from e

        try:
            process_xform(xform, hq_server)
        except IntegrityError as e:
            if "unique_xform_entity_deliver_unit" in str(e):
                logger.info(f"Duplicate form with ID: {xform.id} received.")
            elif "unique_xform_completed_module" in str(e):
                logger.info(f"Learn Module is already completed with form ID: {xform.id}.")
            else:
                raise
        return Response(status=status.HTTP_200_OK)
