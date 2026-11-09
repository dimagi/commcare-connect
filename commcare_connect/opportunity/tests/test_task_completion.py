import uuid
from unittest import mock

import pytest
from rest_framework.test import APIClient

from commcare_connect.opportunity.models import AssignedTask, TaskTypeModeChoices
from commcare_connect.opportunity.tests.factories import AssignedTaskFactory
from commcare_connect.utils.tests.api import add_token_credentials

CALLBACK_URL = "/api/task_completed/"


def _ocs_task(**kwargs):
    return AssignedTaskFactory(task_type__mode=TaskTypeModeChoices.OCS, **kwargs)


def _authed_client(user):
    client = APIClient()
    add_token_credentials(api_client=client, user=user, token="task-callback-token")
    return client


@pytest.mark.django_db
class TestTaskCompletedView:
    def test_requires_authentication(self):
        response = APIClient().post(CALLBACK_URL, data={"connectTaskId": str(uuid.uuid4())}, format="json")
        assert response.status_code == 401

    def test_marks_task_completed(self, user):
        task = _ocs_task()
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
