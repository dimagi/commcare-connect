"""
Database Management Service for Tasks Application

This service handles database cleanup and management operations for the tasks system.
"""

from django.db import transaction

from commcare_connect.tasks.models import Task, TaskAISession, TaskComment, TaskEvent


def get_database_stats():
    """
    Get current counts of tasks-related database records.

    Returns:
        dict: Counts of key database records
    """
    return {
        "tasks": Task.objects.count(),
        "events": TaskEvent.objects.count(),
        "comments": TaskComment.objects.count(),
        "ai_sessions": TaskAISession.objects.count(),
    }


def reset_tasks_database():
    """
    Reset all tasks-related database tables.

    This function clears all tasks data. It does not affect:
    - User accounts
    - Organizations
    - Opportunities

    Deletes:
    - All Task records
    - All TaskEvent records (timeline/activity)
    - All TaskComment records
    - All TaskAISession records

    Returns:
        dict: Counts of deleted records
    """
    # Count before deletion
    deleted = {
        "tasks": Task.objects.count(),
        "events": TaskEvent.objects.count(),
        "comments": TaskComment.objects.count(),
        "ai_sessions": TaskAISession.objects.count(),
    }

    # Use transaction to ensure all-or-nothing
    with transaction.atomic():
        # Delete in correct order to respect foreign keys
        # Child models first, then parent
        TaskComment.objects.all().delete()
        TaskAISession.objects.all().delete()
        TaskEvent.objects.all().delete()
        Task.objects.all().delete()

    return deleted
