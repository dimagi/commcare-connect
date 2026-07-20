import datetime
import uuid
from unittest import mock

import pytest
from django.http import Http404
from django.utils.timezone import now
from rest_framework.test import APIClient

from commcare_connect.opportunity.api.views.task_completion import complete_task
from commcare_connect.opportunity.models import AssignedTask
from commcare_connect.opportunity.tests.factories import AssignedTaskFactory

CALLBACK_URL = "/api/task_completed/"


def _authed_client(user):
    client = APIClient()
    token, _ = user.oauth2_provider_accesstoken.get_or_create(
        token="task-callback-token",
        scope="read write",
        defaults={"expires": now() + datetime.timedelta(hours=1)},
    )
    client.credentials(Authorization=f"Bearer {token}")
    return client


@pytest.mark.django_db
class TestCompleteTask:
    def test_marks_task_completed(self):
        task = AssignedTaskFactory()

        with mock.patch.object(AssignedTask, "mark_completed") as mark_completed:
            result = complete_task(task.assigned_task_id)

        mark_completed.assert_called_once_with(completed_at=None)
        assert result == task

    def test_passes_completed_at_through(self):
        task = AssignedTaskFactory()
        ts = now()

        with mock.patch.object(AssignedTask, "mark_completed") as mark_completed:
            complete_task(task.assigned_task_id, completed_at=ts)

        mark_completed.assert_called_once_with(completed_at=ts)

    def test_raises_404_for_unknown_task(self):
        with pytest.raises(Http404):
            complete_task(uuid.uuid4())


@pytest.mark.django_db
class TestTaskCompletedView:
    def test_requires_authentication(self):
        response = APIClient().post(CALLBACK_URL, data={"connectTaskId": str(uuid.uuid4())}, format="json")
        assert response.status_code == 401

    def test_marks_task_completed(self, user):
        task = AssignedTaskFactory()
        client = _authed_client(user)

        with mock.patch.object(AssignedTask, "mark_completed") as mark_completed:
            response = client.post(CALLBACK_URL, data={"connectTaskId": str(task.assigned_task_id)}, format="json")

        assert response.status_code == 200
        mark_completed.assert_called_once()

    def test_returns_404_for_unknown_task(self, user):
        client = _authed_client(user)
        response = client.post(CALLBACK_URL, data={"connectTaskId": str(uuid.uuid4())}, format="json")
        assert response.status_code == 404

    def test_rejects_payload_without_task_id(self, user):
        client = _authed_client(user)
        response = client.post(CALLBACK_URL, data={}, format="json")
        assert response.status_code == 400
