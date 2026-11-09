import uuid
from datetime import date, timedelta
from unittest import mock

import pytest
from django.utils.timezone import now

from commcare_connect.connect_id_client.models import Message
from commcare_connect.opportunity.exceptions import TaskAlreadyAssignedError
from commcare_connect.opportunity.models import AssignedTask, AssignedTaskStatus, TaskTypeModeChoices
from commcare_connect.opportunity.services.tasks import (
    assign_task,
    complete_ocs_task,
    complete_task,
    delete_assigned_tasks,
    send_task_assigned_message,
    send_task_completed_message,
)
from commcare_connect.opportunity.tests.factories import (
    AssignedTaskFactory,
    OpportunityAccessFactory,
    OpportunityFactory,
    TaskTypeFactory,
)
from commcare_connect.utils.commcarehq_api import CommCareHQAPIException
from commcare_connect.utils.ocs_api import OcsApiError

HQ_UPDATE = "commcare_connect.commcarehq.api.bulk_update_usercases"
NOTIFY_COMPLETION = "commcare_connect.opportunity.tasks.send_task_completion_notification.delay"


@pytest.mark.django_db
class TestAssignTask:
    def test_creates_row_and_pushes_to_hq(self):
        access = OpportunityAccessFactory()
        task_type = TaskTypeFactory(app=access.opportunity.deliver_app, case_property="needs_assessment")
        due_date = date.today() + timedelta(days=7)
        assigner = access.user

        with mock.patch(HQ_UPDATE) as mock_update:
            assigned = assign_task(
                task_type=task_type,
                opportunity_access=access,
                due_date=due_date,
                assigned_by=assigner,
            )

        mock_update.assert_called_once_with({access: {"properties": {"needs_assessment": "1"}}})
        assert isinstance(assigned, AssignedTask)
        assert assigned.task_type == task_type
        assert assigned.opportunity_access == access
        assert assigned.due_date == due_date
        assert assigned.status == AssignedTaskStatus.ASSIGNED
        assert assigned.assigned_by == assigner

    @pytest.mark.parametrize("case_property", [None, ""])
    def test_skips_hq_when_case_property_missing(self, case_property):
        access = OpportunityAccessFactory()
        task_type = TaskTypeFactory(app=access.opportunity.deliver_app, case_property=case_property)
        due_date = date.today() + timedelta(days=7)

        with mock.patch(HQ_UPDATE) as mock_update:
            assigned = assign_task(
                task_type=task_type,
                opportunity_access=access,
                due_date=due_date,
            )

        mock_update.assert_not_called()
        assert AssignedTask.objects.filter(pk=assigned.pk).exists()

    def test_raises_when_task_type_already_assigned(self):
        access = OpportunityAccessFactory()
        task_type = TaskTypeFactory(app=access.opportunity.deliver_app)
        due_date = date.today() + timedelta(days=7)
        AssignedTaskFactory(task_type=task_type, opportunity_access=access, status=AssignedTaskStatus.ASSIGNED)

        with pytest.raises(TaskAlreadyAssignedError):
            assign_task(task_type=task_type, opportunity_access=access, due_date=due_date)

        assert AssignedTask.objects.filter(task_type=task_type, opportunity_access=access).count() == 1

    def test_allows_reassign_after_completion(self):
        access = OpportunityAccessFactory()
        task_type = TaskTypeFactory(app=access.opportunity.deliver_app)
        due_date = date.today() + timedelta(days=7)
        AssignedTaskFactory(task_type=task_type, opportunity_access=access, status=AssignedTaskStatus.COMPLETED)

        assigned = assign_task(task_type=task_type, opportunity_access=access, due_date=due_date)

        assert AssignedTask.objects.filter(task_type=task_type, opportunity_access=access).count() == 2
        assert assigned.status == AssignedTaskStatus.ASSIGNED

    def test_does_not_create_row_when_hq_call_fails(self):
        access = OpportunityAccessFactory()
        task_type = TaskTypeFactory(app=access.opportunity.deliver_app, case_property="needs_assessment")
        due_date = date.today() + timedelta(days=7)

        with mock.patch(
            HQ_UPDATE,
            side_effect=CommCareHQAPIException("boom"),
        ):
            with pytest.raises(CommCareHQAPIException):
                assign_task(
                    task_type=task_type,
                    opportunity_access=access,
                    due_date=due_date,
                )

        assert not AssignedTask.objects.filter(opportunity_access=access, task_type=task_type).exists()

    def test_triggers_ocs_bot_and_persists_session_for_ocs_mode(self):
        access = OpportunityAccessFactory()
        access.user.phone_number = "+15551234567"
        access.user.save()
        task_type = TaskTypeFactory(
            app=access.opportunity.deliver_app,
            mode=TaskTypeModeChoices.OCS,
            ocs_chatbot_id="exp-uuid",
        )
        due_date = date.today() + timedelta(days=7)
        assigner = access.user

        with mock.patch(
            "commcare_connect.utils.ocs_api.trigger_bot",
            return_value=("sess-1", "chan-1"),
        ) as mock_trigger:
            assigned = assign_task(
                task_type=task_type,
                opportunity_access=access,
                due_date=due_date,
                assigned_by=assigner,
            )

        mock_trigger.assert_called_once_with(
            assigner,
            identifier=access.user.username,
            experiment="exp-uuid",
            participant_data={"connectTaskId": str(assigned.assigned_task_id)},
        )
        assigned.refresh_from_db()
        assert assigned.ocs_session_id == "sess-1"
        assert assigned.connect_channel_id == "chan-1"

    def test_does_not_trigger_ocs_bot_for_non_ocs_mode(self):
        access = OpportunityAccessFactory()
        task_type = TaskTypeFactory(app=access.opportunity.deliver_app, mode=TaskTypeModeChoices.RELEARN)
        due_date = date.today() + timedelta(days=7)

        with mock.patch("commcare_connect.utils.ocs_api.trigger_bot") as mock_trigger:
            assign_task(task_type=task_type, opportunity_access=access, due_date=due_date)

        mock_trigger.assert_not_called()

    def test_does_not_create_row_when_ocs_trigger_fails(self):
        access = OpportunityAccessFactory()
        access.user.phone_number = "+15551234567"
        access.user.save()
        task_type = TaskTypeFactory(
            app=access.opportunity.deliver_app,
            mode=TaskTypeModeChoices.OCS,
            ocs_chatbot_id="exp-uuid",
            case_property="needs_assessment",
        )
        due_date = date.today() + timedelta(days=7)

        with (
            mock.patch(
                "commcare_connect.utils.ocs_api.trigger_bot",
                side_effect=OcsApiError("boom"),
            ),
            mock.patch(HQ_UPDATE) as mock_update,
        ):
            with pytest.raises(OcsApiError):
                assign_task(
                    task_type=task_type,
                    opportunity_access=access,
                    due_date=due_date,
                    assigned_by=access.user,
                )

        assert not AssignedTask.objects.filter(opportunity_access=access, task_type=task_type).exists()
        mock_update.assert_not_called()

    def test_ocs_mode_skips_hq_even_with_case_property(self):
        access = OpportunityAccessFactory()
        access.user.phone_number = "+15551234567"
        access.user.save()
        task_type = TaskTypeFactory(
            app=access.opportunity.deliver_app,
            mode=TaskTypeModeChoices.OCS,
            ocs_chatbot_id="exp-uuid",
            case_property="needs_assessment",
        )
        due_date = date.today() + timedelta(days=7)

        with (
            mock.patch(
                "commcare_connect.utils.ocs_api.trigger_bot",
                return_value=("s", "c"),
            ) as mock_trigger,
            mock.patch(HQ_UPDATE) as mock_hq,
        ):
            assign_task(
                task_type=task_type,
                opportunity_access=access,
                due_date=due_date,
                assigned_by=access.user,
            )

        mock_trigger.assert_called_once()
        mock_hq.assert_not_called()


@pytest.mark.django_db
class TestCompleteTask:
    def test_completes_and_notifies(self, django_capture_on_commit_callbacks):
        task = AssignedTaskFactory(status=AssignedTaskStatus.ASSIGNED)
        completed_at = now()

        with mock.patch(NOTIFY_COMPLETION) as notify, django_capture_on_commit_callbacks(execute=True):
            complete_task(task, completed_at=completed_at, xform_id="form-1")

        task.refresh_from_db()
        assert task.status == AssignedTaskStatus.COMPLETED
        assert task.completed_at == completed_at
        assert task.xform_id == "form-1"
        notify.assert_called_once_with(task.pk)

    @pytest.mark.parametrize("days_ago, moves_forward", [(2, True), (None, True), (-1, False)])
    def test_moves_last_active_forward(self, days_ago, moves_forward):
        task = AssignedTaskFactory(status=AssignedTaskStatus.ASSIGNED)
        access = task.opportunity_access
        original = None if days_ago is None else now() - timedelta(days=days_ago)
        access.last_active = original
        access.save(update_fields=["last_active"])
        completed_at = now()

        complete_task(task, completed_at=completed_at)

        access.refresh_from_db()
        assert access.last_active == (completed_at if moves_forward else original)

    def test_does_nothing_when_already_completed(self, django_capture_on_commit_callbacks):
        original = now() - timedelta(days=1)
        task = AssignedTaskFactory(status=AssignedTaskStatus.COMPLETED, completed_at=original)
        access = task.opportunity_access
        access.last_active = None
        access.save(update_fields=["last_active"])

        with mock.patch(NOTIFY_COMPLETION) as notify, django_capture_on_commit_callbacks(execute=True):
            complete_task(task, completed_at=now())

        task.refresh_from_db()
        access.refresh_from_db()
        assert task.completed_at == original
        assert access.last_active is None
        notify.assert_not_called()


@pytest.mark.django_db
class TestCompleteOcsTask:
    def test_completes_task(self):
        task = AssignedTaskFactory(task_type__mode=TaskTypeModeChoices.OCS)

        result = complete_ocs_task(connect_task_id=task.assigned_task_id)

        task.refresh_from_db()
        assert result == task
        assert task.status == AssignedTaskStatus.COMPLETED

    def test_passes_completed_at_through(self):
        task = AssignedTaskFactory(task_type__mode=TaskTypeModeChoices.OCS)
        ts = now() - timedelta(hours=1)

        complete_ocs_task(connect_task_id=task.assigned_task_id, completed_at=ts)

        task.refresh_from_db()
        assert task.completed_at == ts

    def test_raises_for_unknown_task(self):
        with pytest.raises(AssignedTask.DoesNotExist):
            complete_ocs_task(connect_task_id=uuid.uuid4())

    def test_raises_for_non_ocs_task(self):
        task = AssignedTaskFactory(task_type__mode=TaskTypeModeChoices.RELEARN)
        with pytest.raises(AssignedTask.DoesNotExist):
            complete_ocs_task(connect_task_id=task.assigned_task_id)

    def test_second_callback_is_a_noop(self):
        task = AssignedTaskFactory(task_type__mode=TaskTypeModeChoices.OCS, status=AssignedTaskStatus.ASSIGNED)

        complete_ocs_task(connect_task_id=task.assigned_task_id)
        task.refresh_from_db()
        assert task.status == AssignedTaskStatus.COMPLETED
        first_completed_at = task.completed_at

        with mock.patch(NOTIFY_COMPLETION) as notify:
            complete_ocs_task(connect_task_id=task.assigned_task_id)

        task.refresh_from_db()
        assert task.completed_at == first_completed_at
        notify.assert_not_called()


def _task(access, *, case_property="needs_assessment", **kwargs):
    return AssignedTaskFactory(
        opportunity_access=access,
        task_type__app=access.opportunity.deliver_app,
        task_type__case_property=case_property,
        **kwargs,
    )


@pytest.mark.django_db
class TestDeleteAssignedTasks:
    @pytest.fixture
    def access(self):
        return OpportunityAccessFactory()

    @pytest.mark.parametrize("case_property, resets_hq", [("needs_assessment", True), (None, False), ("", False)])
    def test_deletes_row_and_resets_hq_case_property(self, access, case_property, resets_hq):
        task = _task(access, case_property=case_property)

        with mock.patch(HQ_UPDATE) as mock_update:
            deleted = delete_assigned_tasks(task_ids=[task.pk], opportunity=access.opportunity)

        assert deleted == 1
        assert not AssignedTask.objects.filter(pk=task.pk).exists()
        expected_calls = [mock.call({access: {"properties": {case_property: ""}}})] if resets_hq else []
        assert mock_update.call_args_list == expected_calls

    def test_does_not_delete_when_hq_fails(self, access):
        task = _task(access)

        with mock.patch(HQ_UPDATE, side_effect=CommCareHQAPIException("boom")):
            with pytest.raises(CommCareHQAPIException):
                delete_assigned_tasks(task_ids=[task.pk], opportunity=access.opportunity)

        assert AssignedTask.objects.filter(pk=task.pk).exists()

    def test_skips_completed_tasks(self, access):
        completed = _task(access, status=AssignedTaskStatus.COMPLETED)

        with mock.patch(HQ_UPDATE) as mock_update:
            deleted = delete_assigned_tasks(task_ids=[completed.pk], opportunity=access.opportunity)

        assert deleted == 0
        assert AssignedTask.objects.filter(pk=completed.pk).exists()
        mock_update.assert_not_called()

    def test_skips_hq_when_other_assigned_task_remains(self, access):
        task_to_delete = _task(access)
        _task(access)

        with mock.patch(HQ_UPDATE) as mock_update:
            deleted = delete_assigned_tasks(task_ids=[task_to_delete.pk], opportunity=access.opportunity)

        assert deleted == 1
        assert not AssignedTask.objects.filter(pk=task_to_delete.pk).exists()
        mock_update.assert_not_called()

    def test_deduplicates_hq_calls(self, access):
        tasks = [_task(access) for _ in range(2)]

        with mock.patch(HQ_UPDATE) as mock_update:
            delete_assigned_tasks(task_ids=[t.pk for t in tasks], opportunity=access.opportunity)

        mock_update.assert_called_once_with({access: {"properties": {"needs_assessment": ""}}})

    def test_merges_multiple_properties_per_user(self, access):
        task_a = _task(access, case_property="prop_a")
        task_b = _task(access, case_property="prop_b")

        with mock.patch(HQ_UPDATE) as mock_update:
            delete_assigned_tasks(task_ids=[task_a.pk, task_b.pk], opportunity=access.opportunity)

        mock_update.assert_called_once_with({access: {"properties": {"prop_a": "", "prop_b": ""}}})


@pytest.mark.django_db
@pytest.mark.parametrize(
    "send_notification, title, body, key",
    [
        (
            send_task_assigned_message,
            "New Task Assigned",
            "A task has been assigned to you. You must complete it before continuing Delivery activities.",
            "task_assignment",
        ),
        (
            send_task_completed_message,
            "Task Completed",
            "You have completed the task '{task_name}'.",
            "task_completion",
        ),
    ],
    ids=["assignment", "completion"],
)
@mock.patch("commcare_connect.opportunity.services.tasks.send_message")
def test_send_task_message(send_message_patch, send_notification, title, body, key):
    opportunity = OpportunityFactory()
    access = OpportunityAccessFactory(opportunity=opportunity)
    task_type = TaskTypeFactory(app=opportunity.deliver_app)
    assigned_task = AssignedTaskFactory(opportunity_access=access, task_type=task_type)

    send_notification(assigned_task.pk)

    assert send_message_patch.call_count == 1
    send_message_patch.assert_called_with(
        Message(
            usernames=[access.user.username],
            data={
                "action": "ccc_generic_opportunity",
                "title": title,
                "body": body.format(task_name=task_type.name),
                "opportunity_uuid": str(opportunity.opportunity_id),
                "opportunity_status": "delivery",
                "key": key,
                "session_endpoint_id": "cc_app_home",
            },
        )
    )
