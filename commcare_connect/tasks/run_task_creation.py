#!/usr/bin/env python
"""
Task Creation Script

This script creates sample tasks for testing and development purposes.
It follows patterns similar to the audit integration script but focused on task creation.

Workflow:
    1. Clear database (optional)
    2. Ensure required data exists (users, opportunities)
    3. Create tasks based on configuration
    4. Create events, comments, and AI sessions
    5. Verify results

Usage:
    python commcare_connect/tasks/run_task_creation.py [config_key]

    Available configs:
        readers_nigeria     - Reading Glasses Nigeria scenario (Task #1001)
        multiple_warnings   - Multiple warning tasks across opportunities
        escalation_pattern  - Pattern showing warning -> deactivation flow
        ai_interactions     - Tasks with AI assistant interactions
"""

import os
import sys
from dataclasses import dataclass
from datetime import timedelta

# Add project root to Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

# Configure Django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")

import django  # noqa: E402

django.setup()

from django.contrib.auth import get_user_model  # noqa: E402
from django.db import transaction  # noqa: E402
from django.utils import timezone  # noqa: E402

from commcare_connect.opportunity.models import Opportunity, OpportunityAccess  # noqa: E402
from commcare_connect.organization.models import Organization, UserOrganizationMembership  # noqa: E402
from commcare_connect.tasks.models import Task, TaskAISession, TaskComment, TaskEvent  # noqa: E402

User = get_user_model()


# ============================================================================
# CONFIGURATION
# ============================================================================


@dataclass
class TaskCreationConfig:
    """Configuration for task creation run."""

    # Display
    name: str  # Human-readable name for this configuration

    # Options
    clear_db_before: bool = True  # Whether to clear tasks database before running
    create_sample_data: bool = True  # Whether to create sample opportunities/users if needed

    # Task specifications
    tasks: list = None  # List of task specifications to create

    def __post_init__(self):
        if self.tasks is None:
            self.tasks = []


# ============================================================================
# TASK CONFIGURATIONS
# ============================================================================

CONFIGS = {
    "readers_nigeria": TaskCreationConfig(
        name="Reading Glasses Distribution - Nigeria (Task #1001)",
        clear_db_before=True,
        create_sample_data=True,
        tasks=[
            {
                "flw_name": "Amina Okafor",
                "flw_email": "amina.okafor@example.com",
                "opportunity_name": "Reading Glasses Distribution - Nigeria",
                "task_type": "warning",
                "status": "unassigned",
                "priority": "medium",
                "title": "Photo Quality Issues - Glasses Distribution",
                "description": (
                    "Photo quality issues detected during audit. The 'glasses on face' images were not taken "
                    "from the proper angle. Photos must be head-on, from shoulders up, to properly verify "
                    "glasses fit. A warning has been sent to the worker and they have been assigned the Image "
                    "Capture learning module."
                ),
                "audit_session_id": 2058,
                "learning_assignment": (
                    "Image Capture - Review proper photography techniques for glasses fitting documentation"
                ),
                "assigned_to_name": "Chidinma Adewale",
                "created_by_name": "Michael Chen",
                "events": [
                    {
                        "type": "created",
                        "actor": "Michael Chen",
                        "description": "Task created due to audit failure",
                        "minutes_ago": 60,
                    },
                    {
                        "type": "learning_assigned",
                        "actor": "Michael Chen",
                        "description": (
                            "Connect Learn module assigned: Image Capture - Review proper photography "
                            "techniques for glasses fitting documentation"
                        ),
                        "minutes_ago": 48,
                    },
                    {
                        "type": "learning_completed",
                        "actor": "Amina Okafor",
                        "description": ("Completed Image Capture learning module (Score: 95%) - Duration: 18 minutes"),
                        "minutes_ago": 24,
                    },
                    {
                        "type": "ai_conversation",
                        "actor": "AI Assistant",
                        "description": "AI assistant conversation: Post-learning check-in with Amina Okafor",
                        "minutes_ago": 6,
                    },
                ],
                "comments": [
                    {
                        "author_name": "Chidinma Adewale",
                        "content": (
                            "I reviewed the submitted photos. Most 'glasses on face' shots are taken at an "
                            "angle or from too far away. The worker needs to ensure photos are head-on from "
                            "shoulders up to properly verify the glasses fit. I've assigned Learning for the "
                            "Image Capture module."
                        ),
                        "minutes_ago": 50,
                    },
                ],
                # No AI session initially - will be added manually to test the form
                "ai_sessions": [],
                "past_tasks": [
                    {
                        "title": "Incomplete beneficiary information in forms",
                        "task_type": "warning",
                        "status": "resolved",
                        "days_ago": 60,
                    },
                ],
            }
        ],
    ),
    "multiple_warnings": TaskCreationConfig(
        name="Multiple Warning Tasks Across Opportunities",
        clear_db_before=True,
        create_sample_data=True,
        tasks=[
            {
                "flw_name": "David Martinez",
                "flw_email": "david.martinez@example.com",
                "opportunity_name": "Education Assessment 2025",
                "task_type": "warning",
                "status": "network_manager",
                "priority": "high",
                "title": "Data Quality Issues",
                "description": "Multiple form fields left incomplete during recent visits.",
                "audit_session_id": 2055,
                "assigned_to_name": "Robert Brown",
                "created_by_name": "Michael Chen",
                "events": [
                    {
                        "type": "created",
                        "actor": "Michael Chen",
                        "description": "Task created due to audit failure",
                        "minutes_ago": 120,
                    },
                    {
                        "type": "assigned",
                        "actor": "Michael Chen",
                        "description": "Assigned to Robert Brown",
                        "minutes_ago": 115,
                    },
                ],
                "comments": [],
            },
            {
                "flw_name": "Emily Chen",
                "flw_email": "emily.chen@example.com",
                "opportunity_name": "Health Survey Q4 2025",
                "task_type": "warning",
                "status": "resolved",
                "priority": "low",
                "title": "GPS Accuracy Issues",
                "description": "GPS coordinates showing low accuracy for several visits.",
                "audit_session_id": 2061,
                "assigned_to_name": "Jane Smith",
                "created_by_name": "Michael Chen",
                "events": [
                    {
                        "type": "created",
                        "actor": "Michael Chen",
                        "description": "Task created due to audit failure",
                        "minutes_ago": 7200,  # 5 days ago
                    },
                    {
                        "type": "commented",
                        "actor": "Jane Smith",
                        "description": (
                            "FLW has acknowledged and committed to ensuring GPS is enabled before starting visits."
                        ),
                        "minutes_ago": 7000,
                    },
                    {
                        "type": "status_changed",
                        "actor": "Jane Smith",
                        "description": "Status changed to Resolved",
                        "minutes_ago": 6900,
                    },
                ],
                "comments": [
                    {
                        "author_name": "Jane Smith",
                        "content": (
                            "FLW has acknowledged and committed to ensuring GPS is enabled before starting visits."
                        ),
                        "minutes_ago": 7000,
                    },
                ],
            },
        ],
    ),
    "escalation_pattern": TaskCreationConfig(
        name="Escalation Pattern - Warning to Deactivation",
        clear_db_before=True,
        create_sample_data=True,
        tasks=[
            {
                "flw_name": "Maria Garcia",
                "flw_email": "maria.garcia@example.com",
                "opportunity_name": "Community Outreach Program",
                "task_type": "deactivation",
                "status": "network_manager",
                "priority": "high",
                "title": "Repeated Quality Issues - Deactivation",
                "description": (
                    "Serious quality issues detected during audit. This is the 3rd action ticket for this FLW "
                    "in 30 days. The worker has been temporarily deactivated from this opportunity."
                ),
                "audit_session_id": 2056,
                "assigned_to_name": "Sarah Lee",
                "created_by_name": "Michael Chen",
                "events": [
                    {
                        "type": "created",
                        "actor": "Michael Chen",
                        "description": "Task created - pattern of recurring issues detected",
                        "minutes_ago": 4320,  # 3 days ago
                    },
                    {
                        "type": "pattern_detected",
                        "actor": "System",
                        "description": "This is the 3rd action ticket for this FLW in 30 days - auto-escalated",
                        "minutes_ago": 4317,
                    },
                    {
                        "type": "status_changed",
                        "actor": "System",
                        "description": "User temporarily deactivated due to repeated violations",
                        "minutes_ago": 4315,
                    },
                ],
                "comments": [
                    {
                        "author_name": "Sarah Lee",
                        "content": (
                            "I've attempted to reach the FLW multiple times today but no response. This is "
                            "concerning given the pattern. Will try again tomorrow."
                        ),
                        "minutes_ago": 4140,
                    },
                ],
                "past_tasks": [
                    {
                        "title": "Photo quality issues",
                        "task_type": "warning",
                        "status": "resolved",
                        "days_ago": 15,
                    },
                    {
                        "title": "Incomplete form submissions",
                        "task_type": "warning",
                        "status": "resolved",
                        "days_ago": 25,
                    },
                ],
            },
        ],
    ),
    "ai_interactions": TaskCreationConfig(
        name="Tasks with AI Assistant Interactions",
        clear_db_before=True,
        create_sample_data=True,
        tasks=[
            {
                "flw_name": "Jennifer Davis",
                "flw_email": "jennifer.davis@example.com",
                "opportunity_name": "Health Survey Q4 2025",
                "task_type": "warning",
                "status": "action_underway",
                "priority": "medium",
                "title": "Form Completion Training Needed",
                "description": (
                    "Several forms submitted with incomplete required fields. AI assistant engaged for training "
                    "support."
                ),
                "audit_session_id": 2062,
                "assigned_to_name": "Jane Smith",
                "created_by_name": "Michael Chen",
                "events": [
                    {
                        "type": "created",
                        "actor": "Michael Chen",
                        "description": "Task created due to audit failure",
                        "minutes_ago": 180,
                    },
                    {
                        "type": "ai_conversation",
                        "actor": "Jane Smith",
                        "description": "AI assistant conversation initiated with Jennifer Davis",
                        "minutes_ago": 120,
                    },
                ],
                "comments": [
                    {
                        "author_name": "Jane Smith",
                        "content": (
                            "Initiated AI assistant to help FLW understand proper form completion procedures. "
                            "Monitoring progress."
                        ),
                        "minutes_ago": 119,
                    },
                ],
                "ai_sessions": [
                    {
                        "session_id": "ocs-session-1002",
                        "status": "active",
                        "minutes_ago": 120,
                    },
                ],
            },
        ],
    ),
}


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================


def ensure_user(name, email, is_staff=False, is_superuser=False):
    """Get or create a user."""
    user, created = User.objects.get_or_create(
        email=email,
        defaults={
            "username": email,
            "name": name,
            "is_staff": is_staff,
            "is_superuser": is_superuser,
        },
    )
    if created:
        user.set_password("testpassword")
        user.save()
    return user, created


def ensure_opportunity(name, org):
    """Get or create an opportunity."""
    opportunity, created = Opportunity.objects.get_or_create(
        name=name,
        defaults={
            "organization": org,
            "description": f"Sample opportunity: {name}",
            "active": True,
            "is_test": True,
            "created_by": "system",
            "modified_by": "system",
        },
    )
    return opportunity, created


def clear_tasks_database():
    """Clear all tasks data."""
    print("\n" + "=" * 80)
    print("CLEARING TASKS DATABASE")
    print("=" * 80)

    with transaction.atomic():
        counts = {
            "comments": TaskComment.objects.count(),
            "ai_sessions": TaskAISession.objects.count(),
            "events": TaskEvent.objects.count(),
            "tasks": Task.objects.count(),
        }

        TaskComment.objects.all().delete()
        TaskAISession.objects.all().delete()
        TaskEvent.objects.all().delete()
        Task.objects.all().delete()

    for model, count in counts.items():
        print(f"  > Deleted {count} {model}")

    print("\n> Tasks database cleared successfully!")


def create_task_from_spec(spec, config):
    """Create a task and related objects from a specification."""
    now = timezone.now()

    # Ensure organization
    org, org_created = Organization.objects.get_or_create(
        slug="sample-org",
        defaults={
            "name": "Sample Organization",
            "created_by": "system",
            "modified_by": "system",
        },
    )
    if org_created:
        print(f"  Created organization: {org.name}")

    # Ensure FLW user
    flw_user, flw_created = ensure_user(spec["flw_name"], spec["flw_email"])
    if flw_created:
        print(f"  Created FLW user: {flw_user.name}")

    # Ensure opportunity
    opportunity, opp_created = ensure_opportunity(spec["opportunity_name"], org)
    if opp_created:
        print(f"  Created opportunity: {opportunity.name}")

    # Ensure FLW has opportunity access
    access, access_created = OpportunityAccess.objects.get_or_create(
        user=flw_user,
        opportunity=opportunity,
        defaults={"accepted": True},
    )
    if access_created:
        print(f"  Created opportunity access for {flw_user.name}")

    # Ensure created_by user (PM)
    created_by_user, _ = ensure_user(
        spec.get("created_by_name", "System"),
        f"{spec.get('created_by_name', 'system').lower().replace(' ', '.')}@example.com",
        is_staff=True,
    )

    # Make organization a program manager org and user an admin
    org.program_manager = True
    org.save(update_fields=["program_manager"])

    UserOrganizationMembership.objects.get_or_create(
        user=created_by_user,
        organization=org,
        defaults={
            "role": "admin",
            "accepted": True,
        },
    )

    # Ensure assigned_to user if specified (Network Manager - admin but not in PM org)
    assigned_to_user = None
    if spec.get("assigned_to_name"):
        assigned_email = f"{spec['assigned_to_name'].lower().replace(' ', '.')}@example.com"
        assigned_to_user, _ = ensure_user(spec["assigned_to_name"], assigned_email)

        # Create a separate organization for network manager
        nm_org, _ = Organization.objects.get_or_create(
            slug="network-org",
            defaults={
                "name": "Network Organization",
                "program_manager": False,  # Network manager org
                "created_by": "system",
                "modified_by": "system",
            },
        )

        UserOrganizationMembership.objects.get_or_create(
            user=assigned_to_user,
            organization=nm_org,
            defaults={
                "role": "admin",
                "accepted": True,
            },
        )

    # Create the task
    task = Task.objects.create(
        user=flw_user,
        opportunity=opportunity,
        created_by_user=created_by_user,
        assigned_to=assigned_to_user,
        task_type=spec["task_type"],
        status=spec["status"],
        priority=spec["priority"],
        title=spec["title"],
        description=spec["description"],
        learning_assignment_text=spec.get("learning_assignment", ""),
        audit_session_id=spec.get("audit_session_id"),
        created_by=created_by_user.email,
        modified_by=created_by_user.email,
    )
    print(f"\n  > Created task #{task.id}: {task.title}")

    # Create events
    for event_spec in spec.get("events", []):
        event_actor_user = None
        if event_spec["actor"] == flw_user.name:
            event_actor_user = flw_user
        elif event_spec["actor"] == created_by_user.name:
            event_actor_user = created_by_user
        elif assigned_to_user and event_spec["actor"] == assigned_to_user.name:
            event_actor_user = assigned_to_user

        event = TaskEvent.objects.create(
            task=task,
            event_type=event_spec["type"],
            actor=event_spec["actor"],
            actor_user=event_actor_user,
            description=event_spec["description"],
            created_by="system",
            modified_by="system",
        )
        # Adjust timestamp
        event.date_created = now - timedelta(minutes=event_spec["minutes_ago"])
        event.save(update_fields=["date_created"])
        event_mins_ago = event_spec["minutes_ago"]
        print(f"    - Event: {event_spec['type']} ({event_mins_ago} min ago)")

    # Create comments
    for comment_spec in spec.get("comments", []):
        # Find comment author
        comment_author = created_by_user
        if assigned_to_user and comment_spec["author_name"] == assigned_to_user.name:
            comment_author = assigned_to_user

        comment = TaskComment.objects.create(
            task=task,
            author=comment_author,
            content=comment_spec["content"],
            created_by=comment_author.email,
            modified_by=comment_author.email,
        )
        # Adjust timestamp
        comment.date_created = now - timedelta(minutes=comment_spec["minutes_ago"])
        comment.save(update_fields=["date_created"])
        comment_mins_ago = comment_spec["minutes_ago"]
        print(f"    - Comment by {comment_author.name} ({comment_mins_ago} min ago)")

    # Create AI sessions
    for ai_spec in spec.get("ai_sessions", []):
        # Prepare conversation metadata if present
        session_metadata = {}
        if "conversation" in ai_spec:
            # Store conversation with adjusted timestamps
            conversation = []
            for msg in ai_spec["conversation"]:
                conversation.append(
                    {
                        "actor": msg["actor"],
                        "message": msg["message"],
                        "timestamp": (now - timedelta(minutes=msg["minutes_ago"])).isoformat(),
                    }
                )
            session_metadata["conversation"] = conversation

        ai_session = TaskAISession.objects.create(
            task=task,
            ocs_session_id=ai_spec["session_id"],
            status=ai_spec["status"],
            session_metadata=session_metadata,
            created_by="system",
            modified_by="system",
        )
        # Adjust timestamp
        ai_session.date_created = now - timedelta(minutes=ai_spec["minutes_ago"])
        ai_session.save(update_fields=["date_created"])

        msg_count = len(ai_spec.get("conversation", []))
        if msg_count > 0:
            print(f"    - AI Session: {ai_spec['session_id']} ({ai_spec['status']}) - {msg_count} messages")
        else:
            print(f"    - AI Session: {ai_spec['session_id']} ({ai_spec['status']})")

    # Create past tasks for history
    for past_spec in spec.get("past_tasks", []):
        past_task = Task.objects.create(
            user=flw_user,
            opportunity=opportunity,
            created_by_user=created_by_user,
            task_type=past_spec["task_type"],
            status=past_spec["status"],
            priority="medium",
            title=past_spec["title"],
            description=f"Previous issue: {past_spec['title']}",
            created_by=created_by_user.email,
            modified_by=created_by_user.email,
        )
        # Adjust timestamp
        past_task.date_created = now - timedelta(days=past_spec["days_ago"])
        past_task.save(update_fields=["date_created"])
        print(f"    - Past task: {past_spec['title']} ({past_spec['days_ago']} days ago)")

    return task


def run_config(config_key):
    """Run a specific configuration."""
    if config_key not in CONFIGS:
        print(f"Error: Unknown configuration '{config_key}'")
        print(f"\nAvailable configurations: {', '.join(CONFIGS.keys())}")
        return False

    config = CONFIGS[config_key]

    print("\n" + "=" * 80)
    print(f"RUNNING: {config.name}")
    print("=" * 80)

    # Clear database if requested
    if config.clear_db_before:
        clear_tasks_database()

    # Create tasks
    print("\n" + "=" * 80)
    print("CREATING TASKS")
    print("=" * 80)

    created_tasks = []
    with transaction.atomic():
        for task_spec in config.tasks:
            task = create_task_from_spec(task_spec, config)
            created_tasks.append(task)

    # Summary
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"> Created {len(created_tasks)} task(s)")
    print(f"> Total tasks in database: {Task.objects.count()}")
    print(f"> Total events: {TaskEvent.objects.count()}")
    print(f"> Total comments: {TaskComment.objects.count()}")
    print(f"> Total AI sessions: {TaskAISession.objects.count()}")

    print("\n> Configuration completed successfully!")
    print("\nView tasks at: http://localhost:8000/tasks/")

    return True


# ============================================================================
# MAIN
# ============================================================================


def main():
    if len(sys.argv) < 2:
        print("Usage: python run_task_creation.py [config_key]")
        print("\nAvailable configurations:")
        for key, config in CONFIGS.items():
            print(f"  {key:20s} - {config.name}")
        sys.exit(1)

    config_key = sys.argv[1]
    success = run_config(config_key)

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
