"""Reads about task types and assigned tasks."""

from django.db.models import Count, F, OuterRef, Q, QuerySet, Subquery

from commcare_connect.opportunity.models import (
    AssignedTask,
    AssignedTaskStatus,
    CommCareApp,
    Opportunity,
    OpportunityAccess,
    TaskType,
    UserInvite,
)


def get_worker_tasks_base_queryset(opportunity: Opportunity) -> QuerySet[OpportunityAccess]:
    """Return the annotated queryset for the worker tasks table.

    One row per (worker, active task) pair. Only workers with active tasks
    are included. Ordered by user name then task creation date, as required
    by `GroupedByWorkerMixin` for correct row grouping.
    """
    return (
        OpportunityAccess.objects.filter(opportunity=opportunity, accepted=True)
        .select_related("user")
        .annotate(
            status=Subquery(UserInvite.objects.filter(opportunity_access=OuterRef("pk")).values("status")[:1]),
            task_name=F("assignedtask__task_type__name"),
            task_id=F("assignedtask__task_type__id"),
            date_assigned=F("assignedtask__date_created"),
            task_due_date=F("assignedtask__due_date"),
            task_status=F("assignedtask__status"),
            task_is_active=F("assignedtask__task_type__is_active"),
        )
        .filter(task_is_active=True)
        .order_by("user__name", "assignedtask__date_created")
    )


def get_assigned_task_counts(opportunity: Opportunity) -> dict[str, int]:
    """Count the opportunity's tasks: `total_tasks`, `open_tasks` and `complete_tasks`."""
    return AssignedTask.objects.filter(opportunity_access__opportunity=opportunity).aggregate(
        total_tasks=Count("id"),
        open_tasks=Count("id", filter=Q(status=AssignedTaskStatus.ASSIGNED)),
        complete_tasks=Count("id", filter=Q(status=AssignedTaskStatus.COMPLETED)),
    )


def get_assignable_task_types(
    *, opportunity: Opportunity, access: OpportunityAccess | None = None
) -> QuerySet[TaskType]:
    """Active task types of the opportunity's deliver app, minus those `access` already has open."""
    task_types = TaskType.objects.filter(app=opportunity.deliver_app, is_active=True)
    if access is not None:
        already_assigned = AssignedTask.objects.filter(
            opportunity_access=access,
            status=AssignedTaskStatus.ASSIGNED,
        ).values_list("task_type_id", flat=True)
        task_types = task_types.exclude(pk__in=already_assigned)
    return task_types


def has_blocking_pending_task(*, access: OpportunityAccess, app: CommCareApp) -> bool:
    """Whether the worker has an open task of an active, unarchived task type on `app`."""
    return AssignedTask.objects.filter(
        opportunity_access=access,
        status=AssignedTaskStatus.ASSIGNED,
        task_type__app=app,
        task_type__archived__isnull=True,
        task_type__is_active=True,
    ).exists()
