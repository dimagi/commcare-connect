import uuid

from django.conf import settings
from django.db import models

from commcare_connect.opportunity.models import Opportunity, OpportunityAccess
from commcare_connect.utils.db import BaseModel


class AuditReport(BaseModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        COMPLETED = "completed", "Completed"

    audit_report_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    opportunity = models.ForeignKey(Opportunity, on_delete=models.CASCADE, related_name="audit_reports")
    period_start = models.DateField()
    period_end = models.DateField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="completed_audit_reports",
    )
    completed_date = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-period_end", "-date_created"]
        indexes = [
            models.Index(fields=["opportunity", "-period_end"]),
        ]

    def __str__(self):
        return f"AuditReport({self.opportunity_id}, {self.period_start}..{self.period_end})"


class AuditReportEntry(BaseModel):
    class ReviewAction(models.TextChoices):
        NONE = "none", "No action"
        TASKS_ASSIGNED = "tasks_assigned", "Tasks assigned"

    audit_report_entry_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    audit_report = models.ForeignKey(AuditReport, on_delete=models.CASCADE, related_name="entries")
    opportunity_access = models.ForeignKey(
        OpportunityAccess, on_delete=models.CASCADE, related_name="audit_report_entries"
    )
    results = models.JSONField(default=dict)
    flagged = models.BooleanField(default=False)
    reviewed = models.BooleanField(default=False)
    review_action = models.CharField(max_length=32, choices=ReviewAction.choices, null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["audit_report", "flagged", "reviewed"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["audit_report", "opportunity_access"],
                name="unique_entry_per_access_per_report",
            )
        ]

    def __str__(self):
        return f"AuditReportEntry({self.audit_report_id}, {self.opportunity_access_id})"
