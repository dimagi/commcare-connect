"""Task types and the tasks assigned to workers."""

from __future__ import annotations

from uuid import uuid4

import pghistory
from django.db import IntegrityError, models, transaction
from django.db.models import Q
from django.utils.timezone import now
from django.utils.translation import gettext

from commcare_connect.opportunity.exceptions import ListTooLongError, TaskAlreadyAssignedError
from commcare_connect.opportunity.models.access import OpportunityAccess
from commcare_connect.opportunity.models.base import CHOICE_FIELD_MAX_LENGTH, XFormBaseModel
from commcare_connect.opportunity.models.commcare_app import CommCareApp
from commcare_connect.opportunity.models.opportunity import Opportunity
from commcare_connect.users.models import User
from commcare_connect.utils import ocs_api


class TaskTypeModeChoices(models.TextChoices):
    RELEARN = "relearn", gettext("Relearn")
    OCS = "ocs", gettext("Open Chat Studio")


class TaskType(models.Model):
    task_type_id = models.UUIDField(editable=False, default=uuid4, unique=True)
    app = models.ForeignKey(CommCareApp, on_delete=models.CASCADE, related_name="tasks")
    opportunity = models.ForeignKey(Opportunity, on_delete=models.CASCADE, null=True, blank=True)
    slug = models.SlugField()
    unit_name = models.CharField(max_length=255, null=True, blank=True)
    name = models.CharField(max_length=255)
    description = models.TextField()
    case_property = models.CharField(max_length=255, null=True, blank=True)
    archived = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    duration = models.IntegerField(null=True, blank=True)
    mode = models.CharField(
        choices=TaskTypeModeChoices,
        default=TaskTypeModeChoices.RELEARN,
        max_length=CHOICE_FIELD_MAX_LENGTH,
    )
    ocs_chatbot_id = models.CharField(max_length=255, null=True, blank=True)

    def __str__(self):
        return self.name

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["app_id", "slug"], name="unique_task_type_per_app"),
        ]


class AssignedTaskStatus(models.TextChoices):
    ASSIGNED = "assigned", gettext("assigned")
    COMPLETED = "completed", gettext("completed")


@pghistory.track(pghistory.UpdateEvent(), fields=["due_date"])
class AssignedTask(XFormBaseModel):
    assigned_task_id = models.UUIDField(editable=False, default=uuid4, unique=True)
    task_type = models.ForeignKey(TaskType, on_delete=models.PROTECT)
    opportunity_access = models.ForeignKey(OpportunityAccess, on_delete=models.CASCADE)
    completed_at = models.DateTimeField(null=True)
    duration = models.DurationField(null=True)
    xform_id = models.CharField(max_length=50, null=True)
    status = models.CharField(
        choices=AssignedTaskStatus,
        default=AssignedTaskStatus.ASSIGNED,
        max_length=CHOICE_FIELD_MAX_LENGTH,
    )
    due_date = models.DateField()
    date_created = models.DateTimeField(auto_now_add=True)
    date_modified = models.DateTimeField(auto_now=True)
    connect_channel_id = models.CharField(max_length=255, null=True, blank=True)
    ocs_session_id = models.CharField(max_length=255, null=True, blank=True)
    assigned_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="assigned_tasks",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["xform_id", "task_type", "opportunity_access"], name="unique_xform_assigned_task"
            ),
            models.UniqueConstraint(
                fields=["task_type", "opportunity_access"],
                condition=Q(status=AssignedTaskStatus.ASSIGNED),
                name="unique_assigned_task_type_per_access",
            ),
        ]

    @classmethod
    def assign(cls, *, task_type, opportunity_access, due_date, assigned_by=None) -> AssignedTask:
        from commcare_connect.commcarehq.api import bulk_update_usercases
        from commcare_connect.opportunity.tasks import send_task_assignment_notification

        with transaction.atomic():
            try:
                assigned_task = cls.objects.create(
                    task_type=task_type,
                    opportunity_access=opportunity_access,
                    status=AssignedTaskStatus.ASSIGNED,
                    due_date=due_date,
                    assigned_by=assigned_by,
                )
            except IntegrityError:
                raise TaskAlreadyAssignedError(f"Task type '{task_type}' could not be assigned.")

            if task_type.mode == TaskTypeModeChoices.OCS:
                assigned_task.trigger_ocs_bot(assigned_by)
            elif task_type.case_property:
                bulk_update_usercases({opportunity_access: {"properties": {task_type.case_property: "1"}}})
            transaction.on_commit(lambda: send_task_assignment_notification.delay(assigned_task.pk))
        return assigned_task

    def trigger_ocs_bot(self, assigned_by):
        task_user = self.opportunity_access.user

        session_id, channel_id = ocs_api.trigger_bot(
            assigned_by,
            identifier=task_user.username or task_user.phone_number,
            experiment=self.task_type.ocs_chatbot_id,
            participant_data={"connectTaskId": str(self.assigned_task_id)},
        )
        self.ocs_session_id = session_id
        self.connect_channel_id = channel_id
        self.save(update_fields=["ocs_session_id", "connect_channel_id"])

    def mark_completed(
        self,
        *,
        completed_at=None,
        xform_id=None,
        duration=None,
        app_build_id=None,
        app_build_version=None,
    ):
        """Mark this task complete and notify the assignee."""
        from commcare_connect.opportunity.tasks import send_task_completion_notification

        if self.status == AssignedTaskStatus.COMPLETED:
            return

        self.status = AssignedTaskStatus.COMPLETED
        self.completed_at = completed_at or now()
        self.xform_id = xform_id
        self.duration = duration
        self.app_build_id = app_build_id
        self.app_build_version = app_build_version
        self.save(
            update_fields=[
                "status",
                "completed_at",
                "xform_id",
                "duration",
                "app_build_id",
                "app_build_version",
                "date_modified",
            ]
        )

        access = self.opportunity_access
        if not access.last_active or access.last_active < self.completed_at:
            access.last_active = self.completed_at
            access.save(update_fields=["last_active"])

        transaction.on_commit(lambda: send_task_completion_notification.delay(self.pk))

    @classmethod
    def bulk_delete(cls, task_ids: list[int], opportunity: Opportunity) -> int:
        from commcare_connect.commcarehq.api import HQ_CASE_BULK_CHUNK_SIZE, bulk_update_usercases

        tasks = list(
            cls.objects.filter(
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
                cls.objects.filter(pk__in=[t.pk for t in tasks]).exclude(status=AssignedTaskStatus.COMPLETED).delete()
            )
            if hq_updates:
                still_assigned = set(
                    cls.objects.filter(
                        opportunity_access_id__in={access_id for access_id, _ in hq_updates},
                        status=AssignedTaskStatus.ASSIGNED,
                        task_type__case_property__in={p for _, p in hq_updates},
                    ).values_list("opportunity_access_id", "task_type__case_property")
                )
                to_reset: dict[OpportunityAccess, dict] = {}
                for access_id, prop in hq_updates.keys() - still_assigned:
                    access = hq_updates[access_id, prop]
                    to_reset.setdefault(access, {"properties": {}})["properties"][prop] = ""
                if len(to_reset) > HQ_CASE_BULK_CHUNK_SIZE:
                    raise ListTooLongError(
                        f"Too many HQ case property resets ({len(to_reset)}); split the delete into smaller batches."
                    )
                if to_reset:
                    bulk_update_usercases(to_reset)

        return deleted_count
