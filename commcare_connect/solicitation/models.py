from uuid import uuid4

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from commcare_connect.opportunity.models import Country, Currency, DeliveryType
from commcare_connect.organization.models import Organization
from commcare_connect.program.models import Program, ProgramApplication
from commcare_connect.utils.db import BaseModel


class Solicitation(BaseModel):
    """The posting itself — an RFP or EOI shown on the marketplace."""

    class Type(models.TextChoices):
        RFP = "rfp", _("Request for Proposals")
        EOI = "eoi", _("Expression of Interest")

    class Status(models.TextChoices):
        DRAFT = "draft", _("Draft")
        ACTIVE = "active", _("Active")
        CLOSED = "closed", _("Closed")
        CANCELLED = "cancelled", _("Cancelled")

    solicitation_id = models.UUIDField(editable=False, default=uuid4, unique=True)
    title = models.CharField(max_length=255)
    type = models.CharField(max_length=10, choices=Type.choices)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    public = models.BooleanField(default=False)

    # The scope of work; the UI may label this "Scope of work" or "Description".
    description = models.TextField()

    # budget_min is informational; budget_max caps the cumulative active Award.award_amount.
    budget_min = models.PositiveBigIntegerField(null=True, blank=True)
    budget_max = models.PositiveBigIntegerField(null=True, blank=True)
    # Awards are made in this currency.
    currency = models.ForeignKey(Currency, on_delete=models.PROTECT)
    country = models.ForeignKey(Country, on_delete=models.PROTECT, null=True)
    delivery_type = models.ForeignKey(DeliveryType, on_delete=models.PROTECT, null=True, blank=True)

    expected_start_date = models.DateField(null=True, blank=True)
    expected_end_date = models.DateField(null=True, blank=True)
    application_deadline = models.DateTimeField()
    estimated_scale = models.CharField(max_length=255, blank=True)  # e.g. "5,000 households, 3 districts"
    contact_email = models.EmailField()

    # Solicitations may be program-less (exploratory EOIs); organization is the posting PM org.
    program = models.ForeignKey(Program, on_delete=models.PROTECT, null=True, blank=True)
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT)

    hide_scores_until_submit = models.BooleanField(default=True)
    closure_reason = models.TextField(blank=True)  # given when a PM closes early or cancels

    def __str__(self):
        return self.title

    @property
    def structure_locked(self):
        """Questions and criteria are fixed once the solicitation leaves draft, so applicants answer the same form."""
        return self.status != self.Status.DRAFT


# Closed and cancelled solicitations are finished, so only these can still be edited.
EDITABLE_STATUSES = (Solicitation.Status.DRAFT, Solicitation.Status.ACTIVE)


class SolicitationEvaluationCriterion(BaseModel):
    """Internal-only scored dimension. Never shown to applicants or on public pages."""

    solicitation = models.ForeignKey(Solicitation, on_delete=models.CASCADE, related_name="criteria")
    label = models.CharField(max_length=255)
    description = models.TextField(blank=True)  # reviewer guidance ("what good looks like")
    # A percentage; a solicitation's weights must total 100 on publish.
    weight = models.DecimalField(max_digits=5, decimal_places=2)
    display_order = models.PositiveSmallIntegerField(default=0)

    def __str__(self):
        return self.label


class SolicitationQuestion(BaseModel):
    """A question on the application form."""

    class QuestionType(models.TextChoices):
        TEXT = "text", _("Free text")
        NUMBER = "number", _("Number")
        CHOICE = "choice", _("Choice")
        FILE = "file", _("File upload")
        DATE = "date", _("Date")

    solicitation = models.ForeignKey(Solicitation, on_delete=models.CASCADE, related_name="questions")
    text = models.TextField()
    help_text = models.TextField(blank=True)
    question_type = models.CharField(max_length=10, choices=QuestionType.choices)
    choice_options = models.JSONField(null=True, blank=True)  # for CHOICE questions
    required = models.BooleanField(default=True)
    display_order = models.PositiveSmallIntegerField(default=0)
    # Internal link only: a question maps to at most one criterion; a criterion may cover many questions.
    criterion = models.ForeignKey(
        SolicitationEvaluationCriterion, on_delete=models.SET_NULL, null=True, blank=True, related_name="questions"
    )


class SolicitationAttachment(BaseModel):
    """Supporting documents (scope PDFs, reference materials) for applicants."""

    solicitation = models.ForeignKey(Solicitation, on_delete=models.CASCADE, related_name="attachments")
    file = models.FileField(upload_to="solicitations/attachments/")
    filename = models.CharField(max_length=255)


class SolicitationInvitation(BaseModel):
    """Grants an organization access to a private (invite-only) solicitation.

    Public solicitations need no invitation.
    """

    solicitation = models.ForeignKey(Solicitation, on_delete=models.CASCADE, related_name="invitations")
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="solicitation_invitations")
    invited_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["solicitation", "organization"], name="one_invitation_per_org")]


class SolicitationApplication(BaseModel):
    """One submission from an organization in response to a solicitation."""

    class Status(models.TextChoices):
        DRAFT = "draft", _("Draft")
        SUBMITTED = "submitted", _("Submitted")
        UNDER_REVIEW = "under_review", _("Under Review")
        AWARDED = "awarded", _("Awarded")
        REJECTED = "rejected", _("Rejected")
        WITHDRAWN = "withdrawn", _("Withdrawn")

    application_id = models.UUIDField(editable=False, default=uuid4, unique=True)
    solicitation = models.ForeignKey(Solicitation, on_delete=models.CASCADE, related_name="applications")
    # The applying organization is the application's key, matching the
    # Organization-keyed ProgramApplication downstream.
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="solicitation_applications")

    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    # Snapshots, kept even if the user changes or is deleted.
    submitter_name = models.CharField(max_length=255)
    submitter_email = models.EmailField()

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    # Shortlisting is PM curation, orthogonal to status: a shortlisted application stays
    # under_review and reviewers keep scoring it.
    shortlisted = models.BooleanField(default=False, db_index=True)
    submitted_date = models.DateTimeField(null=True, blank=True)
    withdrawn_date = models.DateTimeField(null=True, blank=True)

    # Link into the existing onboarding flow, set on award (program-linked solicitations only).
    program_application = models.ForeignKey(ProgramApplication, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["solicitation", "organization"], name="one_application_per_org")
        ]


class SolicitationApplicationAnswer(BaseModel):
    """An applicant's response to a single question."""

    application = models.ForeignKey(SolicitationApplication, on_delete=models.CASCADE, related_name="answers")
    question = models.ForeignKey(SolicitationQuestion, on_delete=models.CASCADE)
    answer = models.JSONField(null=True, blank=True)  # text / number / choice / date
    file = models.FileField(upload_to="solicitations/answers/", null=True, blank=True)  # for FILE questions

    class Meta:
        constraints = [models.UniqueConstraint(fields=["application", "question"], name="one_answer_per_question")]


class SolicitationReviewer(BaseModel):
    """Grants a user scoped access to review one solicitation."""

    class Role(models.TextChoices):
        REVIEWER = "reviewer", _("Reviewer")  # can score
        OBSERVER = "observer", _("Observer")  # read-only

    solicitation = models.ForeignKey(Solicitation, on_delete=models.CASCADE, related_name="reviewer_assignments")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.REVIEWER)
    invited_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["solicitation", "user"], name="one_assignment_per_user")]


class SolicitationApplicationReview(BaseModel):
    """A single reviewer's scoring of one application."""

    class Recommendation(models.TextChoices):
        APPROVE = "approve", _("Approve")
        REJECT = "reject", _("Reject")
        NEEDS_REVISION = "needs_revision", _("Needs Revision")

    application = models.ForeignKey(SolicitationApplication, on_delete=models.CASCADE, related_name="reviews")
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    recommendation = models.CharField(max_length=20, choices=Recommendation.choices, blank=True)
    # Weighted average of the criterion scores, normalized to /100.
    overall_score = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    notes = models.TextField(blank=True)
    tags = models.JSONField(null=True, blank=True)
    submitted_date = models.DateTimeField(null=True, blank=True)  # null while the review is a draft

    class Meta:
        constraints = [models.UniqueConstraint(fields=["application", "reviewer"], name="one_review_per_reviewer")]


class SolicitationCriterionScore(BaseModel):
    """Per-criterion score within a review; these roll up into SolicitationApplicationReview.overall_score."""

    review = models.ForeignKey(
        SolicitationApplicationReview, on_delete=models.CASCADE, related_name="criterion_scores"
    )
    criterion = models.ForeignKey(SolicitationEvaluationCriterion, on_delete=models.CASCADE)
    score = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(10)])
    comment = models.TextField(blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["review", "criterion"], name="one_score_per_criterion")]


class Award(BaseModel):
    """A decision to award an applicant. A solicitation may have multiple awards."""

    application = models.ForeignKey(SolicitationApplication, on_delete=models.CASCADE, related_name="awards")
    awarded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    # Set when an awarded applicant withdraws: the award's budget is freed and the PM may re-award.
    # Released awards are kept for audit and excluded from the budget cap.
    released_date = models.DateTimeField(null=True, blank=True)
    award_amount = models.PositiveBigIntegerField()  # in the solicitation's currency
    comment = models.TextField(blank=True)
    # v1 placeholder: an out-of-band signed contract uploaded by the PM.
    contract_file = models.FileField(upload_to="solicitations/contracts/", null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["application"],
                condition=models.Q(released_date__isnull=True),
                name="one_active_award_per_application",
            )
        ]
