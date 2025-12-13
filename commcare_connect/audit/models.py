from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class AuditTemplate(models.Model):
    """
    Template/configuration for creating audits.

    Reusable audit templates that define the scope and parameters for audits.
    Can be saved, named, and reused to create multiple audit instances.
    Templates store preview data and sampled visit IDs for consistency.
    """

    # Metadata
    name = models.CharField(max_length=255, blank=True, help_text="Optional name for this audit template")
    description = models.TextField(blank=True, help_text="Optional description of this template's purpose")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="audit_templates",
        help_text="User who created this template",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    expires_at = models.DateTimeField(
        null=True, blank=True, help_text="When this template expires if not used (24h after creation)"
    )

    # Audit Configuration
    opportunity_ids = models.JSONField(help_text="List of opportunity IDs to audit")
    audit_type = models.CharField(
        max_length=50, help_text="Type: date_range, last_n_per_flw, last_n_per_opp, last_n_across_all"
    )
    granularity = models.CharField(max_length=50, help_text="Granularity: combined, per_opp, per_flw")

    # Criteria (optional based on audit_type)
    start_date = models.DateField(null=True, blank=True, help_text="For date_range type")
    end_date = models.DateField(null=True, blank=True, help_text="For date_range type")
    count_per_flw = models.IntegerField(null=True, blank=True, help_text="For last_n_per_flw type")
    count_per_opp = models.IntegerField(null=True, blank=True, help_text="For last_n_per_opp type")
    count_across_all = models.IntegerField(null=True, blank=True, help_text="For last_n_across_all type")

    # Sampling
    sample_percentage = models.IntegerField(default=100, help_text="Percentage of visits to sample (1-100)")
    sampled_visit_ids = models.JSONField(
        null=True, blank=True, help_text="List of sampled visit IDs for reproducibility"
    )

    # Preview Results (calculated during preview step)
    preview_data = models.JSONField(
        null=True,
        blank=True,
        help_text="Preview statistics: FLW counts, visit counts, date ranges per opportunity/granularity",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["created_at"]),
            models.Index(fields=["created_by", "created_at"]),
        ]

    def __str__(self):
        name = self.name or f"Audit Template {self.id}"
        return f"{name} ({self.audit_type}, {len(self.opportunity_ids)} opps)"

    def save(self, *args, **kwargs):
        # Auto-set expiry to 24h after creation if not set
        if not self.pk and not self.expires_at:
            self.expires_at = timezone.now() + timezone.timedelta(hours=24)
        super().save(*args, **kwargs)

    def to_dict(self) -> dict:
        """Export as dictionary for JSON serialization"""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "created_by": self.created_by.username if self.created_by else None,
            "created_at": self.created_at.isoformat(),
            "opportunity_ids": self.opportunity_ids,
            "audit_type": self.audit_type,
            "granularity": self.granularity,
            "start_date": str(self.start_date) if self.start_date else None,
            "end_date": str(self.end_date) if self.end_date else None,
            "count_per_flw": self.count_per_flw,
            "count_per_opp": self.count_per_opp,
            "count_across_all": self.count_across_all,
            "sample_percentage": self.sample_percentage,
            "sampled_visit_ids": self.sampled_visit_ids,
            "preview_data": self.preview_data,
        }

    @classmethod
    def from_dict(cls, data: dict, user=None):
        """Import from dictionary (for import functionality)"""
        from datetime import date

        template = cls(
            name=data.get("name", ""),
            description=data.get("description", ""),
            created_by=user,
            opportunity_ids=data["opportunity_ids"],
            audit_type=data["audit_type"],
            granularity=data["granularity"],
            start_date=date.fromisoformat(data["start_date"]) if data.get("start_date") else None,
            end_date=date.fromisoformat(data["end_date"]) if data.get("end_date") else None,
            count_per_flw=data.get("count_per_flw"),
            count_per_opp=data.get("count_per_opp"),
            count_across_all=data.get("count_across_all"),
            sample_percentage=data.get("sample_percentage", 100),
            sampled_visit_ids=data.get("sampled_visit_ids"),
            preview_data=data.get("preview_data"),
        )
        return template

    def to_criteria_dict(self) -> dict:
        """
        Convert to the criteria dictionary format used by audit services.
        This ensures backward compatibility with existing service APIs.
        """
        criteria = {
            "type": self.audit_type,
            "granularity": self.granularity,
            "samplePercentage": self.sample_percentage,
        }

        if self.audit_type == "last_n_per_flw" and self.count_per_flw:
            criteria["countPerFlw"] = self.count_per_flw
        elif self.audit_type == "last_n_per_opp" and self.count_per_opp:
            criteria["countPerOpp"] = self.count_per_opp
        elif self.audit_type == "last_n_across_all" and self.count_across_all:
            criteria["countAcrossAll"] = self.count_across_all
        elif self.audit_type == "date_range":
            if self.start_date:
                criteria["startDate"] = str(self.start_date)
            if self.end_date:
                criteria["endDate"] = str(self.end_date)

        return criteria

    def get_summary(self) -> dict:
        """Get a human-readable summary of this audit template"""
        summary = {
            "opportunities": len(self.opportunity_ids),
            "audit_type": self.audit_type,
            "granularity": self.granularity,
        }

        if self.audit_type == "date_range":
            summary["date_range"] = f"{self.start_date} to {self.end_date}"
        elif self.audit_type == "last_n_per_flw":
            summary["visits_per_flw"] = self.count_per_flw
        elif self.audit_type == "last_n_per_opp":
            summary["visits_per_opp"] = self.count_per_opp
        elif self.audit_type == "last_n_across_all":
            summary["total_visits"] = self.count_across_all

        if self.sample_percentage < 100:
            summary["sampling"] = f"{self.sample_percentage}%"

        if self.preview_data:
            # Aggregate preview statistics
            total_flws = sum(p.get("total_flws", 0) for p in self.preview_data)
            total_visits = sum(p.get("total_visits", 0) for p in self.preview_data)
            audits = sum(p.get("sessions_to_create", 0) for p in self.preview_data)

            summary["preview"] = {"flws": total_flws, "visits": total_visits, "audits": audits}

        return summary

    @classmethod
    def cleanup_expired(cls):
        """Delete expired unused audit templates"""
        expired = cls.objects.filter(status=cls.Status.READY, expires_at__lt=timezone.now())
        count = expired.count()
        expired.delete()
        return count


# Backward compatibility alias
AuditDefinition = AuditTemplate


class Audit(models.Model):
    """
    An audit of a collection of visits for quality assurance.

    Each audit represents work to be done by an auditor, containing a specific
    set of visits to review. The visits M2M field is the source of truth for
    what's included in this audit.
    """

    class Status(models.TextChoices):
        IN_PROGRESS = "in_progress", _("In Progress")
        COMPLETED = "completed", _("Completed")

    class OverallResult(models.TextChoices):
        PASS = "pass", _("Pass")
        FAIL = "fail", _("Fail")

    # Link to template (optional - for tracking provenance)
    template = models.ForeignKey(
        AuditTemplate,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audits",
        help_text="The audit template used to create this audit",
    )

    # Auditor info
    auditor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="audits_conducted",
        help_text="User conducting the audit",
    )

    # Opportunity link (for single-opportunity audits)
    primary_opportunity = models.ForeignKey(
        "opportunity.Opportunity",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="audits",
        help_text="Primary opportunity for single-opportunity audits",
    )

    # Audit categorization
    title = models.CharField(max_length=255, blank=True, help_text="Audit title (e.g., 'Week of Jan 1-7, 2024')")
    tag = models.CharField(
        max_length=100,
        blank=True,
        help_text="Tag for categorizing audits (e.g., 'first_10', 'quarterly_review')",
    )

    # The exact visits included in this audit (SOURCE OF TRUTH)
    visits = models.ManyToManyField(
        "opportunity.UserVisit",
        related_name="audits",
        help_text="Explicit list of visits included in this audit",
    )

    # Denormalized metadata fields (frozen at creation for display/performance)
    opportunity_name = models.CharField(max_length=255, help_text="Name of opportunity/program being audited")
    start_date = models.DateField(help_text="Start date of audit period (computed from visits at creation)")
    end_date = models.DateField(help_text="End date of audit period (computed from visits at creation)")

    # Audit status and results
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.IN_PROGRESS)
    overall_result = models.CharField(
        max_length=10,
        choices=OverallResult.choices,
        null=True,
        blank=True,
        help_text="Overall audit result for this FLW/period",
    )
    notes = models.TextField(blank=True, help_text="General notes about the audit")
    kpi_notes = models.TextField(blank=True, help_text="KPI-related notes or reasons for failure")

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["auditor", "status"]),
            models.Index(fields=["start_date", "end_date"]),
            models.Index(fields=["status", "created_at"]),
        ]

    def __str__(self):
        flw = self.flw_username or "Multiple FLWs"
        return f"Audit: {flw} ({self.start_date} - {self.end_date})"

    @property
    def flw_username(self):
        """For per_flw audits, return the FLW username. Otherwise empty for combined audits."""
        first_visit = self.visits.first()
        if first_visit:
            return first_visit.user.username
        return ""

    @property
    def domain(self):
        """Get CommCare domain from primary opportunity or first visit's opportunity"""
        opp = self.primary_opportunity
        if not opp:
            first_visit = self.visits.select_related("opportunity__deliver_app").first()
            if first_visit:
                opp = first_visit.opportunity

        if opp and opp.deliver_app:
            return opp.deliver_app.cc_domain
        return ""

    @property
    def app_id(self):
        """Get CommCare app ID from primary opportunity or first visit's opportunity"""
        opp = self.primary_opportunity
        if not opp:
            first_visit = self.visits.select_related("opportunity__deliver_app").first()
            if first_visit:
                opp = first_visit.opportunity

        if opp and opp.deliver_app:
            return opp.deliver_app.cc_app_id
        return ""

    @property
    def opportunity_ids(self):
        """Get all opportunity IDs from visits"""
        return list(self.visits.values_list("opportunity_id", flat=True).distinct())

    @property
    def user_ids(self):
        """Get all user IDs from visits"""
        return list(self.visits.values_list("user_id", flat=True).distinct())

    @property
    def progress_percentage(self):
        """Calculate audit progress as percentage based on assessed images"""
        from commcare_connect.audit.helpers import calculate_audit_progress

        percentage, _, _ = calculate_audit_progress(self)
        return percentage


# Backward compatibility alias
AuditSession = Audit


class AuditResult(models.Model):
    """Result for one visit within an audit"""

    class Result(models.TextChoices):
        PASS = "pass", _("Pass")
        FAIL = "fail", _("Fail")

    audit = models.ForeignKey(Audit, on_delete=models.CASCADE, related_name="results")
    user_visit = models.ForeignKey(
        "opportunity.UserVisit", on_delete=models.CASCADE, help_text="The UserVisit being audited"
    )
    result = models.CharField(
        max_length=10, choices=Result.choices, null=True, blank=True, help_text="Pass or fail for this visit"
    )
    notes = models.TextField(blank=True, help_text="Optional notes about why this visit failed")
    audited_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ["audit", "user_visit"]
        ordering = ["user_visit__visit_date"]
        indexes = [
            models.Index(fields=["audit", "result"]),
        ]

    def __str__(self):
        return f"{self.user_visit} - {self.result}"

    # Backward compatibility property
    @property
    def audit_session(self):
        return self.audit


class Assessment(models.Model):
    """Assessment of one element (image/flag/data) within a visit"""

    class AssessmentType(models.TextChoices):
        IMAGE = "image", _("Image Assessment")
        # Future: FLAG, DATA_ELEMENT, etc.

    class Result(models.TextChoices):
        PASS = "pass", _("Pass")
        FAIL = "fail", _("Fail")
        # Future: NEEDS_REVIEW, etc.

    audit_result = models.ForeignKey(AuditResult, on_delete=models.CASCADE, related_name="assessments")

    assessment_type = models.CharField(
        max_length=50, choices=AssessmentType.choices, help_text="Type of assessment (image, flag, data element, etc.)"
    )

    # For image assessments
    blob_id = models.CharField(max_length=255, blank=True, help_text="Blob ID if this is an image assessment")
    question_id = models.CharField(
        max_length=255, blank=True, help_text="CommCare question ID associated with this assessment"
    )

    # Assessment result
    result = models.CharField(
        max_length=10,
        choices=Result.choices,
        null=True,
        blank=True,
        help_text="Assessment result (null = not yet assessed)",
    )

    notes = models.TextField(blank=True, help_text="Notes about this specific assessment")

    # Future fields for other assessment types
    config_data = models.JSONField(null=True, blank=True, help_text="Configuration data for this assessment type")

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ["audit_result", "assessment_type", "blob_id"]
        ordering = ["question_id", "blob_id"]
        indexes = [
            models.Index(fields=["audit_result", "assessment_type"]),
            models.Index(fields=["audit_result", "result"]),
            models.Index(fields=["blob_id"]),
        ]

    def __str__(self):
        return f"{self.assessment_type} - {self.result or 'pending'}"
