"""Deliver visits submitted by workers and their attachments."""

from uuid import uuid4

from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.utils.dateparse import parse_datetime
from django.utils.timezone import now
from django.utils.translation import gettext, gettext_lazy

from commcare_connect.opportunity.models.access import OpportunityAccess
from commcare_connect.opportunity.models.base import CHOICE_FIELD_MAX_LENGTH, XFormBaseModel
from commcare_connect.opportunity.models.completed_work import CompletedWork
from commcare_connect.opportunity.models.opportunity import Opportunity
from commcare_connect.opportunity.models.payment_units import DeliverUnit
from commcare_connect.users.models import User


class VisitValidationStatus(models.TextChoices):
    pending = "pending", gettext("Pending")
    approved = "approved", gettext("Approved")
    rejected = "rejected", gettext("Rejected")
    over_limit = "over_limit", gettext("Over Limit")
    duplicate = "duplicate", gettext("Duplicate")
    trial = "trial", gettext("Trial")


class OverLimitReasonChoices(models.TextChoices):
    """Which limits barred a visit that was given VisitValidationStatus.over_limit."""

    claim_ended = "claim_ended", gettext("Worker's claim period for the opportunity has ended")
    claim_limit_ended = "claim_limit_ended", gettext("Worker's claim period for the payment unit has ended")
    max_visits = "max_visits", gettext("Worker's total visit limit reached")
    max_daily = "max_daily", gettext("Payment unit's daily visit limit reached")


class VisitReviewStatus(models.TextChoices):
    pending = "pending", gettext("Pending Review")
    agree = "agree", gettext("Agree")
    disagree = "disagree", gettext("Disagree")


class UserVisit(XFormBaseModel):
    user_visit_id = models.UUIDField(editable=False, default=uuid4, unique=True)
    opportunity = models.ForeignKey(
        Opportunity,
        on_delete=models.CASCADE,
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
    )
    opportunity_access = models.ForeignKey(OpportunityAccess, on_delete=models.CASCADE, null=True)
    deliver_unit = models.ForeignKey(DeliverUnit, on_delete=models.PROTECT)
    entity_id = models.CharField(max_length=255, null=True, blank=True)
    entity_name = models.CharField(max_length=255, null=True, blank=True)
    visit_date = models.DateTimeField()
    status = models.CharField(
        max_length=CHOICE_FIELD_MAX_LENGTH,
        choices=VisitValidationStatus,
        default=VisitValidationStatus.pending,
    )
    form_json = models.JSONField()
    # Free text written by org staff when they reject a visit
    reason = models.CharField(max_length=300, null=True, blank=True)
    over_limit_reasons = ArrayField(
        models.CharField(max_length=CHOICE_FIELD_MAX_LENGTH, choices=OverLimitReasonChoices),
        default=list,
        blank=True,
        help_text=gettext_lazy("Every limit that barred this visit, set only when status is over_limit."),
    )
    location = models.CharField(null=True)
    flagged = models.BooleanField(default=False)
    flag_reason = models.JSONField(null=True, blank=True)
    completed_work = models.ForeignKey(CompletedWork, on_delete=models.DO_NOTHING, null=True, blank=True)
    work_area = models.ForeignKey(
        "microplanning.WorkArea",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
    )
    status_modified_date = models.DateTimeField(null=True, default=now)
    review_status = models.CharField(
        max_length=CHOICE_FIELD_MAX_LENGTH, choices=VisitReviewStatus, default=VisitReviewStatus.pending
    )
    review_status_modified_date = models.DateTimeField(blank=True, null=True, default=now)
    review_created_on = models.DateTimeField(blank=True, null=True)
    justification = models.CharField(max_length=300, null=True, blank=True)
    date_created = models.DateTimeField(auto_now_add=True)

    def __init__(self, *args, **kwargs):
        self.status = VisitValidationStatus.pending
        super().__init__(*args, **kwargs)

    def __setattr__(self, name, value):
        if name == "status":
            if getattr(self, "status", None) != value:
                self.status_modified_date = now()
        super().__setattr__(name, value)

    @property
    def images(self):
        return BlobMeta.objects.filter(parent_id=self.xform_id, content_type__startswith="image/")

    @property
    def audio(self):
        return self.audio_attachments.all()

    @property
    def duration(self):
        duration = None
        start = self.form_json["metadata"].get("timeStart")
        end = self.form_json["metadata"].get("timeEnd")
        if start and end:
            try:
                duration = parse_datetime(end) - parse_datetime(start)
            except (TypeError, ValueError):
                pass
        return duration

    @property
    def flags(self):
        if self.flag_reason is not None:
            from commcare_connect.utils.flags import FlagLabels

            flags = [FlagLabels.get_label(flag) for flag, _ in self.flag_reason.get("flags", [])]
            return flags
        return []

    def get_over_limit_reasons_display(self) -> list[str]:
        """Labels for every limit that barred this visit, for showing to users.

        Reasons are kept when a reviewer later changes the visit's status, so they are shown
        whatever the status. Visits that went over limit before reasons were recorded have
        none stored, so they get an explicit "not recorded" label rather than an empty list.
        """
        if self.over_limit_reasons:
            return [OverLimitReasonChoices(reason).label for reason in self.over_limit_reasons]
        if self.status == VisitValidationStatus.over_limit:
            return [gettext("Not recorded (visit predates over-limit reason tracking)")]
        return []

    @property
    def hq_link(self):
        hq_url = self.deliver_unit.app.hq_server.url
        domain = self.opportunity.deliver_app.cc_domain
        return f"{hq_url}/a/{domain}/reports/form_data/{self.xform_id}/"

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["xform_id", "entity_id", "deliver_unit"], name="unique_xform_entity_deliver_unit"
            )
        ]
        indexes = [
            models.Index(fields=["opportunity", "status"]),
        ]


class BlobMeta(models.Model):
    name = models.CharField(max_length=255)
    parent_id = models.CharField(
        max_length=255,
        help_text="Parent primary key or unique identifier",
    )
    blob_id = models.CharField(max_length=255, default=uuid4)
    content_length = models.IntegerField()
    content_type = models.CharField(max_length=255, null=True)

    class Meta:
        unique_together = [
            ("parent_id", "name"),
        ]
        indexes = [models.Index(fields=["blob_id"])]


class AudioAttachment(models.Model):
    user_visit = models.ForeignKey(
        UserVisit,
        on_delete=models.CASCADE,
        related_name="audio_attachments",
    )
    blob_id = models.CharField(max_length=255, default=uuid4, unique=True)
    name = models.CharField(max_length=255)
    content_type = models.CharField(max_length=255, null=True)
    content_length = models.IntegerField()
    transcript = models.TextField(blank=True, default="")
    translation = models.TextField(blank=True, default="")
    date_created = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [
            ("user_visit", "name"),
        ]
