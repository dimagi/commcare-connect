from urllib.parse import urlencode
from uuid import uuid4

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Avg, Count, Exists, F, OuterRef, Q, Subquery
from django.db.models.functions import Coalesce, Now
from django.urls import reverse
from django.utils.timezone import now
from django.utils.translation import gettext_lazy as _

from commcare_connect.opportunity.models import Country, Currency, DeliveryType
from commcare_connect.organization.models import Organization, UserOrganizationMembership
from commcare_connect.program.models import Program, ProgramApplication
from commcare_connect.utils.db import BaseModel


class SolicitationQuerySet(models.QuerySet):
    """A solicitation closes at its deadline even before the sweep task updates its stored status,
    so check openness through open() / Solicitation.is_open, never by comparing status directly."""

    def _active(self):
        return self.filter(status=Solicitation.Status.ACTIVE)

    def open(self):
        return self._active().filter(application_deadline__gt=Now())

    def expired(self):
        return self._active().filter(application_deadline__lte=Now())

    def public(self):
        """Public and still open for applications: what the marketplace shows to everyone."""
        return self.open().filter(public=True)

    def editable(self):
        """Drafts and open solicitations; closed and cancelled ones are finished."""
        return self.filter(status=Solicitation.Status.DRAFT) | self.open()

    def visible_to(self, user):
        """Public solicitations, and private ones that ``user``'s organization posted or was invited to."""
        if not user.is_authenticated:
            return self.filter(public=True)
        invited = SolicitationInvitation.objects.filter(solicitation=OuterRef("pk"), organization__members=user)
        posted = Organization.objects.filter(pk=OuterRef("organization_id"), members=user)
        return self.filter(Q(public=True) | Q(Exists(invited)) | Q(Exists(posted)))


# An open solicitation with fewer days than this left before its deadline is closing soon.
CLOSES_SOON_DAYS = 14


class Solicitation(BaseModel):
    """The posting itself — an RFP or EOI shown on the marketplace."""

    objects = SolicitationQuerySet.as_manager()

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
    def effective_status(self):
        if self.status == self.Status.ACTIVE and self.application_deadline <= now():
            return self.Status.CLOSED
        return self.Status(self.status)

    @property
    def is_open(self):
        return self.effective_status == self.Status.ACTIVE

    @property
    def closes_soon(self):
        """Open, with fewer than CLOSES_SOON_DAYS whole days left."""
        return self.is_open and self.days_until_deadline < CLOSES_SOON_DAYS

    @property
    def days_until_deadline(self):
        """Whole days left before the deadline; 0 on its last day."""
        return (self.application_deadline - now()).days

    @property
    def apply_label(self):
        """What the button that starts an application says."""
        return _("Express interest") if self.type == self.Type.EOI else _("Submit proposal")

    @property
    def is_editable(self):
        """Closed and cancelled solicitations are finished, so only drafts and open ones can be edited."""
        return self.status == self.Status.DRAFT or self.is_open

    @property
    def structure_locked(self):
        """Questions and criteria are fixed once the solicitation leaves draft, so applicants answer the same form."""
        return self.status != self.Status.DRAFT

    def apply_url(self, organization=None):
        """The Apply page, applying as ``organization`` when given, or else asking which organization to use."""
        url = reverse("marketplace:apply", args=[self.solicitation_id])
        return f"{url}?{urlencode({'organization': organization.slug})}" if organization else url

    def applicant_organizations(self, user):
        """The organizations ``user`` can apply as: those they administer, other than the poster. A private
        solicitation only takes applications from the organizations it invited."""
        organizations = Organization.objects.filter(
            memberships__user=user, memberships__role=UserOrganizationMembership.Role.ADMIN
        ).exclude(pk=self.organization_id)
        if not self.public:
            organizations = organizations.filter(solicitation_invitations__solicitation=self)
        return organizations


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
    """Invites an organization to apply to a solicitation.

    Required to apply to a private solicitation; on a public one it just asks a specific organization to apply.
    """

    solicitation = models.ForeignKey(Solicitation, on_delete=models.CASCADE, related_name="invitations")
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="solicitation_invitations")
    invited_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["solicitation", "organization"], name="one_invitation_per_org")]


class SolicitationApplicationQuerySet(models.QuerySet):
    def newest_first(self):
        """Most recently submitted first; pk breaks ties so rows don't move between pages."""
        return self.order_by(F("submitted_date").desc(nulls_last=True), "pk")

    def responses(self):
        return self.exclude(status__in=SolicitationApplication.NON_RESPONSE_STATUSES)

    def with_review_summary(self):
        """Annotates each application from its submitted reviews and its solicitation's reviewers.

        average_score and the <recommendation>_count fields use every submitted review. reviewer_count is the
        number of reviewers on the application's solicitation, and reviewed_count only counts reviews by them, so
        the two measure progress against the current reviewers.
        """
        # Observers don't score, so only reviewers count towards review progress.
        reviewers = SolicitationReviewer.objects.filter(
            solicitation=OuterRef("solicitation"), role=SolicitationReviewer.Role.REVIEWER
        )
        reviewer_count = reviewers.values("solicitation").annotate(count=Count("pk")).values("count")
        submitted = Q(reviews__submitted_date__isnull=False)
        recommendation_counts = {
            f"{recommendation}_count": Count("reviews", filter=submitted & Q(reviews__recommendation=recommendation))
            for recommendation in SolicitationApplicationReview.Recommendation
        }
        return self.annotate(
            average_score=Avg("reviews__overall_score", filter=submitted),
            reviewer_count=Coalesce(Subquery(reviewer_count), 0),
            reviewed_count=Count(
                "reviews", filter=submitted & Q(reviews__reviewer_id__in=reviewers.values("user_id"))
            ),
            **recommendation_counts,
        )


class SolicitationApplication(BaseModel):
    """One submission from an organization in response to a solicitation."""

    objects = SolicitationApplicationQuerySet.as_manager()

    class Status(models.TextChoices):
        DRAFT = "draft", _("Draft")
        SUBMITTED = "submitted", _("Submitted")
        UNDER_REVIEW = "under_review", _("Under Review")
        AWARDED = "awarded", _("Awarded")
        REJECTED = "rejected", _("Rejected")
        WITHDRAWN = "withdrawn", _("Withdrawn")

    # Drafts haven't been sent yet and withdrawn applications were taken back, so neither is a response.
    NON_RESPONSE_STATUSES = (Status.DRAFT, Status.WITHDRAWN)

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

    @property
    def applicant_url(self):
        """Where the applying organization follows this application: its Apply page, which shows it once sent."""
        return self.solicitation.apply_url(self.organization)


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
