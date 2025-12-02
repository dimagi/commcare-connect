from django.db import models
from django.utils.translation import gettext_lazy as _

from commcare_connect.opportunity.models import Opportunity
from commcare_connect.users.models import User
from commcare_connect.utils.db import BaseModel


class TaskType(models.TextChoices):
    WARNING = "warning", _("Warning")
    DEACTIVATION = "deactivation", _("Deactivation")


class TaskStatus(models.TextChoices):
    UNASSIGNED = "unassigned", _("Unassigned")
    NETWORK_MANAGER = "network_manager", _("Network Manager")
    PROGRAM_MANAGER = "program_manager", _("Program Manager")
    ACTION_UNDERWAY = "action_underway", _("Action Underway")
    RESOLVED = "resolved", _("Resolved")
    CLOSED = "closed", _("Closed")


class TaskPriority(models.TextChoices):
    LOW = "low", _("Low")
    MEDIUM = "medium", _("Medium")
    HIGH = "high", _("High")


class Task(BaseModel):
    """Task for tracking actions against FLWs based on various triggers."""

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="tasks",
        help_text="The Field Level Worker this task is about",
    )
    opportunity = models.ForeignKey(
        Opportunity,
        on_delete=models.CASCADE,
        related_name="tasks",
        help_text="The opportunity this task is associated with",
    )
    created_by_user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name="created_tasks",
        help_text="User who created this task",
    )
    assigned_to = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_tasks",
        help_text="User currently assigned to handle this task",
    )

    task_type = models.CharField(
        max_length=20,
        choices=TaskType.choices,
        default=TaskType.WARNING,
    )
    status = models.CharField(
        max_length=20,
        choices=TaskStatus.choices,
        default=TaskStatus.UNASSIGNED,
    )
    priority = models.CharField(
        max_length=10,
        choices=TaskPriority.choices,
        default=TaskPriority.MEDIUM,
    )

    title = models.CharField(max_length=255)
    description = models.TextField()
    learning_assignment_text = models.TextField(
        blank=True,
        help_text="Text description of learning modules or training assigned",
    )

    # Optional fields for future integration
    audit_session_id = models.IntegerField(
        null=True,
        blank=True,
        help_text="Reference to audit session that triggered this task",
    )

    class Meta:
        ordering = ["-date_created"]
        indexes = [
            models.Index(fields=["opportunity", "status"]),
            models.Index(fields=["user", "status"]),
            models.Index(fields=["status", "-date_created"]),
        ]

    def __str__(self):
        return f"Task #{self.id}: {self.user.name} - {self.get_task_type_display()}"

    def can_user_access(self, user):
        """Check if a user can access this task based on opportunity/org permissions."""
        if user.is_superuser:
            return True

        # Check if user has access to the opportunity
        from commcare_connect.opportunity.models import OpportunityAccess

        if OpportunityAccess.objects.filter(opportunity=self.opportunity, user=user).exists():
            return True

        # Check if user is part of the organization
        if user.memberships.filter(organization=self.opportunity.organization).exists():
            return True

        return False


class TaskEventType(models.TextChoices):
    CREATED = "created", _("Created")
    STATUS_CHANGED = "status_changed", _("Status Changed")
    ASSIGNED = "assigned", _("Assigned")
    COMMENTED = "commented", _("Commented")
    LEARNING_ASSIGNED = "learning_assigned", _("Learning Assigned")
    LEARNING_COMPLETED = "learning_completed", _("Learning Completed")
    AI_CONVERSATION = "ai_conversation", _("AI Conversation")
    NOTIFICATION_SENT = "notification_sent", _("Notification Sent")
    FLW_ACKNOWLEDGED = "flw_acknowledged", _("FLW Acknowledged")
    PATTERN_DETECTED = "pattern_detected", _("Pattern Detected")
    OTHER = "other", _("Other")


class TaskEvent(BaseModel):
    """Timeline events for task activity tracking."""

    task = models.ForeignKey(
        Task,
        on_delete=models.CASCADE,
        related_name="events",
    )
    event_type = models.CharField(
        max_length=30,
        choices=TaskEventType.choices,
    )
    actor = models.CharField(
        max_length=255,
        help_text="Name or identifier of who/what performed this action",
    )
    actor_user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="User who performed this action, if applicable",
    )
    ai_session = models.ForeignKey(
        "TaskAISession",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="events",
        help_text="AI session associated with this event (for AI conversation events)",
    )
    description = models.TextField()
    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="Additional structured data about this event",
    )

    class Meta:
        ordering = ["-date_created"]
        indexes = [
            models.Index(fields=["task", "-date_created"]),
        ]

    def __str__(self):
        return f"{self.actor} - {self.get_event_type_display()}"


class TaskComment(BaseModel):
    """User comments on tasks."""

    task = models.ForeignKey(
        Task,
        on_delete=models.CASCADE,
        related_name="comments",
    )
    author = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
    )
    content = models.TextField()

    class Meta:
        ordering = ["-date_created"]
        indexes = [
            models.Index(fields=["task", "-date_created"]),
        ]

    def __str__(self):
        return f"Comment by {self.author.name} on Task #{self.task.id}"


class TaskAISessionStatus(models.TextChoices):
    INITIATED = "initiated", _("Initiated")
    ACTIVE = "active", _("Active")
    COMPLETED = "completed", _("Completed")
    FAILED = "failed", _("Failed")


class TaskAISession(BaseModel):
    """AI assistant conversation sessions linked to tasks."""

    task = models.ForeignKey(
        Task,
        on_delete=models.CASCADE,
        related_name="ai_sessions",
    )
    ocs_session_id = models.CharField(
        max_length=255,
        unique=True,
        null=True,
        blank=True,
        help_text="Session ID from OCS API",
    )
    status = models.CharField(
        max_length=20,
        choices=TaskAISessionStatus.choices,
        default=TaskAISessionStatus.INITIATED,
    )
    session_metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="Metadata for reconnecting to OCS session",
    )

    class Meta:
        ordering = ["-date_created"]

    def __str__(self):
        return f"AI Session {self.ocs_session_id} for Task #{self.task.id}"

    def get_transcript(self):
        """Fetch transcript from OCS API just-in-time."""
        from commcare_connect.tasks.ocs_client import get_transcript

        return get_transcript(self.ocs_session_id)


class OpportunityBotConfiguration(BaseModel):
    """Configuration for OCS bots per opportunity."""

    opportunity = models.OneToOneField(
        Opportunity,
        on_delete=models.CASCADE,
        related_name="bot_configuration",
        help_text="The opportunity this bot configuration is for",
    )
    ocs_bot_id = models.CharField(
        max_length=255,
        help_text="OCS Bot ID for this opportunity",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Whether AI assistant is enabled for this opportunity",
    )
    bot_name = models.CharField(
        max_length=255,
        blank=True,
        help_text="Human-readable bot name for UI display",
    )
    configuration = models.JSONField(
        default=dict,
        blank=True,
        help_text="Additional bot configuration (channel preferences, prompts, etc.)",
    )

    class Meta:
        verbose_name = "Opportunity Bot Configuration"
        verbose_name_plural = "Opportunity Bot Configurations"

    def __str__(self):
        return f"Bot config for {self.opportunity.name}: {self.ocs_bot_id}"
