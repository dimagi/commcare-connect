import math
from uuid import uuid4

from django.conf import settings
from django.db import models
from django.db.models import Sum
from django.utils.timezone import now
from django.utils.translation import gettext

from commcare_connect.organization.models import Organization
from commcare_connect.users.models import User
from commcare_connect.utils.db import BaseModel


class CommCareApp(BaseModel):
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="apps",
        related_query_name="app",
    )
    cc_domain = models.CharField(max_length=255)
    cc_app_id = models.CharField(max_length=50)
    name = models.CharField(max_length=255)
    description = models.TextField()
    passing_score = models.IntegerField(null=True)

    def __str__(self):
        return self.name

    @property
    def url(self):
        return f"{settings.COMMCARE_HQ_URL}/a/{self.cc_domain}/apps/view/{self.cc_app_id}"


class HQApiKey(models.Model):
    api_key = models.CharField(max_length=50, unique=True)
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
    )


class Opportunity(BaseModel):
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="opportunities",
        related_query_name="opportunity",
    )
    name = models.CharField(max_length=255)
    description = models.TextField()
    short_description = models.CharField(max_length=50, null=True)
    active = models.BooleanField(default=True)
    learn_app = models.ForeignKey(
        CommCareApp,
        on_delete=models.CASCADE,
        related_name="learn_app_opportunities",
        null=True,
    )
    deliver_app = models.ForeignKey(
        CommCareApp,
        on_delete=models.CASCADE,
        null=True,
    )
    max_visits_per_user = models.IntegerField(null=True)
    daily_max_visits_per_user = models.IntegerField(null=True)
    end_date = models.DateField(null=True)
    budget_per_visit = models.IntegerField(null=True)
    total_budget = models.IntegerField(null=True)
    api_key = models.ForeignKey(HQApiKey, on_delete=models.DO_NOTHING, null=True)
    currency = models.CharField(max_length=3, null=True)

    def __str__(self):
        return self.name

    @property
    def remaining_budget(self) -> int:
        return self.total_budget - self.claimed_budget

    @property
    def claimed_budget(self):
        return self.claimed_visits * self.budget_per_visit

    @property
    def utilised_budget(self):
        return self.approved_visits * self.budget_per_visit

    @property
    def claimed_visits(self):
        opp_access = OpportunityAccess.objects.filter(opportunity=self)
        used_budget = OpportunityClaim.objects.filter(opportunity_access__in=opp_access).aggregate(
            Sum("max_payments")
        )["max_payments__sum"]
        if used_budget is None:
            used_budget = 0
        return used_budget

    @property
    def approved_visits(self):
        approved_user_visits = UserVisit.objects.filter(
            opportunity=self, status=VisitValidationStatus.approved
        ).count()
        return approved_user_visits

    @property
    def allotted_visits(self):
        return math.floor(self.total_budget / self.budget_per_visit)

    @property
    def budget_per_user(self):
        return self.max_visits_per_user * self.budget_per_visit

    @property
    def is_active(self):
        return self.active and self.end_date >= now().date()


class LearnModule(models.Model):
    app = models.ForeignKey(
        CommCareApp,
        on_delete=models.CASCADE,
        related_name="learn_modules",
    )
    slug = models.SlugField()
    name = models.CharField(max_length=255)
    description = models.TextField()
    time_estimate = models.IntegerField(help_text="Estimated hours to complete the module")

    def __str__(self):
        return self.name


class XFormBaseModel(models.Model):
    xform_id = models.CharField(max_length=50)
    app_build_id = models.CharField(max_length=50, null=True, blank=True)
    app_build_version = models.IntegerField(null=True, blank=True)

    class Meta:
        abstract = True


class CompletedModule(XFormBaseModel):
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="completed_modules",
    )
    module = models.ForeignKey(LearnModule, on_delete=models.PROTECT)
    opportunity = models.ForeignKey(Opportunity, on_delete=models.PROTECT)
    date = models.DateTimeField()
    duration = models.DurationField()


class Assessment(XFormBaseModel):
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="assessments",
    )
    app = models.ForeignKey(CommCareApp, on_delete=models.PROTECT)
    opportunity = models.ForeignKey(Opportunity, on_delete=models.PROTECT)
    date = models.DateTimeField()
    score = models.IntegerField()
    passing_score = models.IntegerField()
    passed = models.BooleanField()


class OpportunityAccess(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    opportunity = models.ForeignKey(Opportunity, on_delete=models.CASCADE)
    date_learn_started = models.DateTimeField(null=True)
    accepted = models.BooleanField(default=False)
    invite_id = models.CharField(max_length=50, default=uuid4)
    payment_accrued = models.PositiveIntegerField(default=0)

    class Meta:
        indexes = [models.Index(fields=["invite_id"])]
        unique_together = ("user", "opportunity")

    # TODO: Convert to a field and calculate this property CompletedModule is saved
    @property
    def learn_progress(self):
        learn_modules = LearnModule.objects.filter(app=self.opportunity.learn_app)
        learn_modules_count = learn_modules.count()
        if learn_modules_count <= 0:
            return 0
        completed_modules = CompletedModule.objects.filter(
            opportunity=self.opportunity, module__in=learn_modules, user=self.user
        ).count()
        percentage = (completed_modules / learn_modules_count) * 100
        return round(percentage, 2)

    @property
    def visit_count(self):
        user_visits = (
            UserVisit.objects.filter(user=self.user_id, opportunity=self.opportunity)
            .exclude(status=VisitValidationStatus.over_limit)
            .order_by("visit_date")
        )
        return user_visits.count()

    @property
    def last_visit_date(self):
        user_visits = (
            UserVisit.objects.filter(user=self.user_id, opportunity=self.opportunity)
            .exclude(status=VisitValidationStatus.over_limit)
            .order_by("visit_date")
        )
        if user_visits.exists():
            return user_visits.last().visit_date
        return

    @property
    def total_paid(self):
        return Payment.objects.filter(opportunity_access=self).aggregate(total=Sum("amount")).get("total", 0)

    @property
    def display_name(self):
        if self.accepted:
            return self.user.name
        else:
            return "---"


class PaymentUnit(models.Model):
    opportunity = models.ForeignKey(Opportunity, on_delete=models.PROTECT)
    amount = models.PositiveIntegerField()
    name = models.CharField(max_length=255)
    description = models.TextField()


class DeliverUnit(models.Model):
    app = models.ForeignKey(
        CommCareApp,
        on_delete=models.CASCADE,
        related_name="deliver_units",
    )
    slug = models.SlugField(max_length=100)
    name = models.CharField(max_length=255)
    payment_unit = models.ForeignKey(
        PaymentUnit,
        on_delete=models.CASCADE,
        related_name="deliver_units",
        related_query_name="deliver_unit",
        null=True,
    )

    def __str__(self):
        return self.name


class VisitValidationStatus(models.TextChoices):
    pending = "pending", gettext("Pending")
    approved = "approved", gettext("Approved")
    rejected = "rejected", gettext("Rejected")
    over_limit = "over_limit", gettext("Over Limit")
    duplicate = "duplicate", gettext("Duplicate")


class Payment(models.Model):
    amount = models.PositiveIntegerField()
    date_paid = models.DateTimeField(auto_now_add=True)
    opportunity_access = models.ForeignKey(OpportunityAccess, on_delete=models.DO_NOTHING, null=True, blank=True)
    payment_unit = models.ForeignKey(
        PaymentUnit,
        on_delete=models.CASCADE,
        related_name="payments",
        related_query_name="payment",
        null=True,
    )


class UserVisit(XFormBaseModel):
    opportunity = models.ForeignKey(
        Opportunity,
        on_delete=models.CASCADE,
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
    )
    deliver_unit = models.ForeignKey(DeliverUnit, on_delete=models.PROTECT)
    entity_id = models.CharField(max_length=255, null=True, blank=True)
    entity_name = models.CharField(max_length=255, null=True, blank=True)
    visit_date = models.DateTimeField()
    status = models.CharField(
        max_length=50, choices=VisitValidationStatus.choices, default=VisitValidationStatus.pending
    )
    form_json = models.JSONField()
    reason = models.CharField(max_length=300, null=True, blank=True)
    location = models.CharField(null=True)
    flagged = models.BooleanField(default=False)
    flag_reason = models.JSONField(null=True, blank=True)

    @property
    def images(self):
        return BlobMeta.objects.filter(parent_id=self.xform_id, content_type__startswith="image/")


class OpportunityClaim(models.Model):
    opportunity_access = models.OneToOneField(OpportunityAccess, on_delete=models.CASCADE)
    max_payments = models.IntegerField()
    end_date = models.DateField()
    date_claimed = models.DateField(auto_now_add=True)


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
