from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from commcare_connect.organization.decorators import IsProgramManagerAdmin
from commcare_connect.program.api.serializers import ProgramCreateSerializer, ProgramResponseSerializer


class ProgramCreateView(APIView):
    permission_classes = [IsAuthenticated, IsProgramManagerAdmin]

    def post(self, request):
        serializer = ProgramCreateSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        program = serializer.save()
        return Response(ProgramResponseSerializer(program).data, status=status.HTTP_201_CREATED)
