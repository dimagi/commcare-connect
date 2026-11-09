from django.http import Http404
from oauth2_provider.contrib.rest_framework import OAuth2Authentication, TokenHasReadWriteScope
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from commcare_connect.opportunity.api.serializers.task_completion import TaskCompletionSerializer
from commcare_connect.opportunity.models import AssignedTask
from commcare_connect.opportunity.services.tasks import complete_ocs_task


class TaskCompletedView(APIView):
    authentication_classes = [OAuth2Authentication]
    permission_classes = [TokenHasReadWriteScope]

    def post(self, request):
        serializer = TaskCompletionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            complete_ocs_task(
                connect_task_id=serializer.validated_data["connectTaskId"],
                completed_at=serializer.validated_data.get("completed_at"),
            )
        except AssignedTask.DoesNotExist:
            raise Http404
        return Response(status=status.HTTP_200_OK)
