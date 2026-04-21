from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from commcare_connect.organization.decorators import IsProgramManagerAdmin


class ProgramCreateView(APIView):
    permission_classes = [IsAuthenticated, IsProgramManagerAdmin]

    def post(self, request):
        return Response(status=status.HTTP_501_NOT_IMPLEMENTED)
