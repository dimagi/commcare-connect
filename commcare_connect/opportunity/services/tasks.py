"""Assign, complete and delete tasks, and keep CommCare HQ, OCS and the worker's phone in step."""

import datetime
from functools import partial
from uuid import UUID

from django.db import IntegrityError, transaction

from commcare_connect.commcarehq import api as commcarehq_api
from commcare_connect.connect_id_client import send_message
from commcare_connect.connect_id_client.models import Message
from commcare_connect.opportunity.exceptions import ListTooLongError, TaskAlreadyAssignedError
from commcare_connect.opportunity.models import (
    AssignedTask,
    AssignedTaskStatus,
    Opportunity,
    OpportunityAccess,
    TaskType,
    TaskTypeModeChoices,
)
from commcare_connect.users.models import User
from commcare_connect.utils import ocs_api


def assign_task(
    *,
    task_type: TaskType,
    opportunity_access: OpportunityAccess,
    due_date: datetime.date,
    assigned_by: User | None = None,
) -> AssignedTask:
    """Assign a task to a worker and notify them.

    OCS tasks start a chatbot session. Other tasks set the task type's case property on the
    worker's HQ usercase. If either call fails, the task is not saved.
    """
    from commcare_connect.opportunity.tasks import send_task_assignment_notification

    with transaction.atomic():
        try:
            assigned_task = AssignedTask.objects.create(
                task_type=task_type,
                opportunity_access=opportunity_access,
                status=AssignedTaskStatus.ASSIGNED,
                due_date=due_date,
                assigned_by=assigned_by,
            )
        except IntegrityError:
            raise TaskAlreadyAssignedError(f"Task type '{task_type}' could not be assigned.")

        if task_type.mode == TaskTypeModeChoices.OCS:
            _start_ocs_session(assigned_task=assigned_task, assigned_by=assigned_by)
        elif task_type.case_property:
            commcarehq_api.bulk_update_usercases({opportunity_access: {"properties": {task_type.case_property: "1"}}})
        transaction.on_commit(partial(send_task_assignment_notification.delay, assigned_task.pk))
    return assigned_task


def _start_ocs_session(*, assigned_task: AssignedTask, assigned_by: User | None) -> None:
    task_user = assigned_task.opportunity_access.user
    session_id, channel_id = ocs_api.trigger_bot(
        assigned_by,
        identifier=task_user.username or task_user.phone_number,
        experiment=assigned_task.task_type.ocs_chatbot_id,
        participant_data={"connectTaskId": str(assigned_task.assigned_task_id)},
    )
    assigned_task.ocs_session_id = session_id
    assigned_task.connect_channel_id = channel_id
    assigned_task.save(update_fields=["ocs_session_id", "connect_channel_id"])


def complete_ocs_task(*, connect_task_id: UUID, completed_at: datetime.datetime | None = None) -> AssignedTask:
    """Complete an OCS task when its chatbot reports it done.

    Raises AssignedTask.DoesNotExist when no OCS task has this id.
    """
    with transaction.atomic():
        assigned_task = AssignedTask.objects.select_for_update().get(
            assigned_task_id=connect_task_id,
            task_type__mode=TaskTypeModeChoices.OCS,
        )
        complete_task(assigned_task, completed_at=completed_at)
    return assigned_task


def complete_task(
    assigned_task: AssignedTask,
    *,
    completed_at: datetime.datetime | None = None,
    xform_id: str | None = None,
    duration: datetime.timedelta | None = None,
    app_build_id: str | None = None,
    app_build_version: int | None = None,
) -> None:
    """Mark a task complete, move the worker's last_active forward and notify them.

    Does nothing if the task is already complete.
    """
    from commcare_connect.opportunity.tasks import send_task_completion_notification

    completed = assigned_task.mark_completed(
        completed_at=completed_at,
        xform_id=xform_id,
        duration=duration,
        app_build_id=app_build_id,
        app_build_version=app_build_version,
    )
    if not completed:
        return

    access = assigned_task.opportunity_access
    if not access.last_active or access.last_active < assigned_task.completed_at:
        access.last_active = assigned_task.completed_at
        access.save(update_fields=["last_active"])

    transaction.on_commit(partial(send_task_completion_notification.delay, assigned_task.pk))


def delete_assigned_tasks(*, task_ids: list[int], opportunity: Opportunity) -> int:
    """Delete the opportunity's open tasks among `task_ids` and return how many were deleted.

    Clears each task type's case property in HQ unless the worker has another open task that
    uses it. Completed tasks are skipped. If HQ fails, nothing is deleted.
    """
    tasks = list(
        AssignedTask.objects.filter(
            pk__in=task_ids,
            opportunity_access__opportunity=opportunity,
        )
        .exclude(status=AssignedTaskStatus.COMPLETED)
        .select_related("task_type", "opportunity_access")
    )
    if not tasks:
        return 0

    hq_updates: dict[tuple[int, str], OpportunityAccess] = {}
    for task in tasks:
        if prop := task.task_type.case_property:
            hq_updates.setdefault((task.opportunity_access_id, prop), task.opportunity_access)

    with transaction.atomic():
        deleted_count, _ = (
            AssignedTask.objects.filter(pk__in=[t.pk for t in tasks])
            .exclude(status=AssignedTaskStatus.COMPLETED)
            .delete()
        )
        if hq_updates:
            _reset_hq_case_properties(hq_updates)

    return deleted_count


def _reset_hq_case_properties(hq_updates: dict[tuple[int, str], OpportunityAccess]) -> None:
    """Clear each (access, case property) pair in HQ unless another open task still uses it."""
    still_assigned = set(
        AssignedTask.objects.filter(
            opportunity_access_id__in={access_id for access_id, _ in hq_updates},
            status=AssignedTaskStatus.ASSIGNED,
            task_type__case_property__in={p for _, p in hq_updates},
        ).values_list("opportunity_access_id", "task_type__case_property")
    )
    to_reset: dict[OpportunityAccess, dict] = {}
    for access_id, prop in hq_updates.keys() - still_assigned:
        access = hq_updates[access_id, prop]
        to_reset.setdefault(access, {"properties": {}})["properties"][prop] = ""
    if len(to_reset) > commcarehq_api.HQ_CASE_BULK_CHUNK_SIZE:
        raise ListTooLongError(
            f"Too many HQ case property resets ({len(to_reset)}); split the delete into smaller batches."
        )
    if to_reset:
        commcarehq_api.bulk_update_usercases(to_reset)


def send_task_assigned_message(assigned_task_id: int) -> None:
    """Tell the worker on their phone that a task was assigned to them."""
    assigned_task = AssignedTask.objects.select_related(
        "opportunity_access__user",
        "opportunity_access__opportunity",
    ).get(pk=assigned_task_id)
    _send_task_message(
        assigned_task=assigned_task,
        title="New Task Assigned",
        body="A task has been assigned to you. You must complete it before continuing Delivery activities.",
        key="task_assignment",
    )


def send_task_completed_message(assigned_task_id: int) -> None:
    """Tell the worker on their phone that they completed a task."""
    assigned_task = AssignedTask.objects.select_related(
        "opportunity_access__user",
        "opportunity_access__opportunity",
        "task_type",
    ).get(pk=assigned_task_id)
    _send_task_message(
        assigned_task=assigned_task,
        title="Task Completed",
        body=f"You have completed the task '{assigned_task.task_type.name}'.",
        key="task_completion",
    )


def _send_task_message(*, assigned_task: AssignedTask, title: str, body: str, key: str) -> None:
    access = assigned_task.opportunity_access
    message = Message(
        usernames=[access.user.username],
        data={
            "action": "ccc_generic_opportunity",
            "title": title,
            "body": body,
            "opportunity_uuid": str(access.opportunity.opportunity_id),
            "opportunity_status": "delivery",
            "key": key,
            "session_endpoint_id": "cc_app_home",
        },
    )
    send_message(message)
