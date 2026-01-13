from django.db import models

from commcare_connect.opportunity.models import OpportunityAccess
from commcare_connect.tasks.models import Task


def user_can_access_task(user, task):
    """
    Check if a user can access a specific task.

    Access is granted if:
    - User is a superuser
    - User has access to the task's opportunity (via OpportunityAccess)
    - User is a member of the task's opportunity's organization
    """
    if user.is_superuser:
        return True

    # Check if user has opportunity access
    if OpportunityAccess.objects.filter(opportunity=task.opportunity, user=user).exists():
        return True

    # Check if user is part of the organization
    if user.memberships.filter(organization=task.opportunity.organization).exists():
        return True

    return False


def get_user_tasks_queryset(user):
    """
    Get a filtered queryset of tasks the user can access.

    Includes tasks from:
    - Opportunities the user has access to
    - Opportunities from organizations the user is a member of
    """
    if user.is_superuser:
        return Task.objects.all()

    # Get opportunities the user has access to via OpportunityAccess
    # or via organization membership
    from commcare_connect.opportunity.models import Opportunity

    accessible_opportunity_ids = OpportunityAccess.objects.filter(user=user).values_list("opportunity_id", flat=True)

    # Get organization IDs where user is a member
    org_ids = user.memberships.values_list("organization_id", flat=True)
    org_opportunity_ids = Opportunity.objects.filter(organization_id__in=org_ids).values_list("id", flat=True)

    # Combine both querysets
    queryset = Task.objects.filter(
        models.Q(opportunity_id__in=accessible_opportunity_ids) | models.Q(opportunity_id__in=org_opportunity_ids)
    ).distinct()

    return queryset


def create_task_from_audit(audit_session_id, user, opportunity, task_type, description, created_by_user, **kwargs):
    """
    Create a task from an audit failure or other trigger.

    This is a clean API for future automation.

    Args:
        audit_session_id: ID of the audit session that triggered this task
        user: The FLW user this task is about
        opportunity: The opportunity this task relates to
        task_type: Type of task (warning/deactivation)
        description: Description of what happened
        created_by_user: User creating the task (or system user)
        **kwargs: Additional fields (priority, assigned_to, etc.)

    Returns:
        The created Task instance
    """
    from commcare_connect.tasks.models import TaskEvent, TaskEventType

    # Create the task
    task = Task.objects.create(
        user=user,
        opportunity=opportunity,
        task_type=task_type,
        description=description,
        audit_session_id=audit_session_id,
        created_by_user=created_by_user,
        created_by=created_by_user.email if created_by_user else "system",
        modified_by=created_by_user.email if created_by_user else "system",
        title=kwargs.get("title", f"{task_type.title()} for {user.name}"),
        priority=kwargs.get("priority", "medium"),
        status=kwargs.get("status", "unassigned"),
        assigned_to=kwargs.get("assigned_to"),
    )

    # Create initial event
    TaskEvent.objects.create(
        task=task,
        event_type=TaskEventType.CREATED,
        actor=created_by_user.name if created_by_user else "System",
        actor_user=created_by_user,
        description=f"Task created from audit session #{audit_session_id}",
        created_by=created_by_user.email if created_by_user else "system",
        modified_by=created_by_user.email if created_by_user else "system",
        metadata={"audit_session_id": audit_session_id},
    )

    return task
