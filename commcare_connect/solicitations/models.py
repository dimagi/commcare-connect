from django.db import models
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from commcare_connect.organization.models import Organization
from commcare_connect.program.models import Program
from commcare_connect.users.models import User
from commcare_connect.utils.db import BaseModel


class SolicitationType(models.TextChoices):
    EOI = "eoi", _("Expression of Interest")
    RFP = "rfp", _("Request for Proposal")


class SolicitationStatus(models.TextChoices):
    DRAFT = "draft", _("Draft")
    ACTIVE = "active", _("Active")
    COMPLETED = "completed", _("Completed")
    CLOSED = "closed", _("Closed")


class Solicitation(BaseModel):
    """
    Unified model for both EOIs and RFPs
    """

    title = models.CharField(max_length=255, verbose_name="Solicitation Title")
    description = models.TextField(
        verbose_name="Description", help_text="Rich text description of the program and requirements"
    )
    target_population = models.CharField(
        max_length=255, help_text="Who will be served (e.g., 'Children under 5 in rural areas')"
    )
    scope_of_work = models.TextField(help_text="What work needs to be done")
    solicitation_type = models.CharField(
        max_length=3, choices=SolicitationType.choices, default=SolicitationType.EOI, verbose_name="Type"
    )
    status = models.CharField(
        max_length=10, choices=SolicitationStatus.choices, default=SolicitationStatus.DRAFT, verbose_name="Status"
    )
    is_publicly_listed = models.BooleanField(
        default=True,
        verbose_name="Publicly Listed",
        help_text="Whether this appears in public listings (can still be accessed via direct URL if False)",
    )

    # Relationships
    program = models.ForeignKey(
        Program,
        on_delete=models.CASCADE,
        related_name="solicitations",
        help_text="The program that owns this solicitation",
    )
    parent_solicitation = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="child_solicitations",
        help_text="For RFPs that follow from EOIs",
    )

    # Timeline
    expected_start_date = models.DateField(null=True, blank=True, verbose_name="Expected Start Date")
    expected_end_date = models.DateField(null=True, blank=True, verbose_name="Expected End Date")
    application_deadline = models.DateField(verbose_name="Application Deadline")

    # Additional details
    estimated_scale = models.CharField(max_length=255, blank=True, help_text="e.g., '40,000 children, 25-50 FLWs'")

    # File attachments
    attachments = models.FileField(
        upload_to="solicitations/attachments/", null=True, blank=True, help_text="Supporting documents"
    )

    class Meta:
        ordering = ["-date_created"]
        indexes = [
            models.Index(fields=["solicitation_type", "status"]),
            models.Index(fields=["is_publicly_listed", "status"]),
            models.Index(fields=["application_deadline"]),
        ]

    def __str__(self):
        return f"{self.get_solicitation_type_display()}: {self.title}"

    def get_absolute_url(self):
        return reverse("solicitations:detail", kwargs={"pk": self.pk})

    @property
    def is_active(self):
        return self.status == SolicitationStatus.ACTIVE

    @property
    def is_publicly_visible(self):
        return self.is_publicly_listed and self.is_active

    @property
    def response_count(self):
        return self.responses.count()

    @property
    def can_accept_responses(self):
        from django.utils import timezone

        return self.is_active and self.application_deadline >= timezone.now().date()


class QuestionType(models.TextChoices):
    TEXT = "text", _("Short Text")
    TEXTAREA = "textarea", _("Long Text")
    NUMBER = "number", _("Number")
    FILE = "file", _("File Upload")
    MULTIPLE_CHOICE = "multiple_choice", _("Multiple Choice")


class SolicitationQuestion(models.Model):
    """
    Questions for each solicitation to enable flexible forms
    """

    solicitation = models.ForeignKey(Solicitation, on_delete=models.CASCADE, related_name="questions")
    question_text = models.TextField()
    question_type = models.CharField(max_length=15, choices=QuestionType.choices, default=QuestionType.TEXTAREA)
    is_required = models.BooleanField(default=True)
    options = models.JSONField(
        null=True, blank=True, help_text="For multiple choice questions, store options as JSON array"
    )
    order = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["order"]
        unique_together = ["solicitation", "order"]

    def __str__(self):
        return f"{self.solicitation.title} - Q{self.order}"


class ResponseStatus(models.TextChoices):
    DRAFT = "draft", _("Draft")
    SUBMITTED = "submitted", _("Submitted")
    UNDER_REVIEW = "under_review", _("Under Review")
    ACCEPTED = "accepted", _("Accepted")
    REJECTED = "rejected", _("Rejected")
    PROGRESSED_TO_RFP = "progressed_to_rfp", _("Progressed to RFP")


class SolicitationResponse(BaseModel):
    """
    Responses submitted by organizations to solicitations
    """

    solicitation = models.ForeignKey(Solicitation, on_delete=models.CASCADE, related_name="responses")
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="solicitation_responses")
    submitted_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name="submitted_responses")
    submission_date = models.DateTimeField(auto_now_add=True)

    # Response data
    responses = models.JSONField(default=dict, help_text="Flexible storage for question/answer pairs")
    status = models.CharField(max_length=20, choices=ResponseStatus.choices, default=ResponseStatus.DRAFT)

    # Progression tracking
    progressed_to_solicitation = models.ForeignKey(
        Solicitation,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="parent_responses",
        help_text="If this EOI response was accepted and progressed to RFP",
    )

    class Meta:
        ordering = ["-submission_date"]
        unique_together = ["solicitation", "organization"]
        indexes = [
            models.Index(fields=["status", "submission_date"]),
            models.Index(fields=["solicitation", "status"]),
        ]

    def __str__(self):
        return f"{self.organization.name} → {self.solicitation.title}"

    @property
    def is_draft(self):
        """Check if this response is still a draft"""
        return self.status == ResponseStatus.DRAFT

    @property
    def is_submitted(self):
        """Check if this response has been submitted"""
        return self.status != ResponseStatus.DRAFT

    def submit(self):
        """Submit the draft response"""
        if self.is_draft:
            self.status = ResponseStatus.SUBMITTED
            self.save(update_fields=["status"])


class ResponseAttachment(BaseModel):
    """
    File attachments for solicitation responses
    Supports multiple files per response
    """

    response = models.ForeignKey(SolicitationResponse, on_delete=models.CASCADE, related_name="file_attachments")
    file = models.FileField(upload_to="solicitations/response_attachments/", help_text="Supporting document")
    original_filename = models.CharField(max_length=255, help_text="Original filename when uploaded")
    file_size = models.PositiveIntegerField(help_text="File size in bytes")
    uploaded_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name="uploaded_attachments")

    class Meta:
        ordering = ["-date_created"]

    def __str__(self):
        return f"{self.original_filename} ({self.response})"

    @property
    def file_size_mb(self):
        """Return file size in MB"""
        return round(self.file_size / (1024 * 1024), 2)

    def delete(self, *args, **kwargs):
        """Delete the file from storage when the model is deleted"""
        if self.file:
            self.file.delete(save=False)
        super().delete(*args, **kwargs)


class ReviewRecommendation(models.TextChoices):
    RECOMMENDED = "recommended", _("Recommended")
    NOT_RECOMMENDED = "not_recommended", _("Not Recommended")
    NEUTRAL = "neutral", _("Neutral")


class SolicitationReview(models.Model):
    """
    Reviews and scoring of responses by program managers
    """

    response = models.ForeignKey(SolicitationResponse, on_delete=models.CASCADE, related_name="reviews")
    reviewer = models.ForeignKey(User, on_delete=models.CASCADE, related_name="solicitation_reviews")
    score = models.PositiveIntegerField(null=True, blank=True, help_text="Numeric score (1-100)")
    tags = models.CharField(max_length=255, blank=True, help_text="Comma-separated tags")
    notes = models.TextField(blank=True)
    recommendation = models.CharField(max_length=15, choices=ReviewRecommendation.choices, null=True, blank=True)
    review_date = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-review_date"]
        unique_together = ["response", "reviewer"]

    def __str__(self):
        return f"Review by {self.reviewer.email} for {self.response}"
