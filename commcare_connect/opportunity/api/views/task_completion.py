from django.db import transaction
from django.shortcuts import get_object_or_404
from oauth2_provider.contrib.rest_framework import OAuth2Authentication, TokenHasReadWriteScope
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from commcare_connect.opportunity.api.serializers.task_completion import TaskCompletionSerializer
from commcare_connect.opportunity.models import AssignedTask
from commcare_connect.opportunity.tasks import send_task_completion_notification


class TaskCompletedView(APIView):
    authentication_classes = [OAuth2Authentication]
    permission_classes = [TokenHasReadWriteScope]

    def post(self, request):
        serializer = TaskCompletionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        complete_task(
            serializer.validated_data["connectTaskId"],
            completed_at=serializer.validated_data.get("completed_at"),
        )
        return Response(status=status.HTTP_200_OK)


def complete_task(connect_task_id, completed_at=None) -> AssignedTask:
    assigned_task = get_object_or_404(AssignedTask, assigned_task_id=connect_task_id)
    assigned_task.mark_completed(completed_at=completed_at)
    transaction.on_commit(lambda: send_task_completion_notification.delay(assigned_task.pk))
    return assigned_task
