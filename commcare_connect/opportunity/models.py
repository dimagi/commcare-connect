import datetime
from collections import Counter, defaultdict
from uuid import uuid4

from django.conf import settings
from django.db import models
from django.db.models import Count, F, Max, Q, Sum
from django.utils.functional import cached_property
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


class DeliveryType(models.Model):
    name = models.CharField(max_length=255)
    slug = models.CharField(max_length=255)
    description = models.CharField(max_length=255)

    def __str__(self):
        return self.name


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
    # to be removed
    max_visits_per_user = models.IntegerField(null=True)
    daily_max_visits_per_user = models.IntegerField(null=True)
    start_date = models.DateField(default=datetime.date.today)
    end_date = models.DateField(null=True)
    # to be removed
    budget_per_visit = models.IntegerField(null=True)
    total_budget = models.IntegerField(null=True)
    api_key = models.ForeignKey(HQApiKey, on_delete=models.DO_NOTHING, null=True)
    currency = models.CharField(max_length=3, null=True)
    auto_approve_visits = models.BooleanField(default=False)
    auto_approve_payments = models.BooleanField(default=False)
    is_test = models.BooleanField(default=True)
    delivery_type = models.ForeignKey(DeliveryType, null=True, blank=True, on_delete=models.DO_NOTHING)

    def __str__(self):
        return self.name

    @property
    def is_setup_complete(self):
        if not (self.paymentunit_set.count() > 0 and self.total_budget and self.start_date and self.end_date):
            return False
        for pu in self.paymentunit_set.all():
            if not (pu.max_total and pu.max_daily):
                return False
        return True

    @property
    def minimum_budget_per_visit(self):
        return min(self.paymentunit_set.all().values_list("amount", flat=True))

    @property
    def remaining_budget(self) -> int:
        return self.total_budget - self.claimed_budget

    @property
    def claimed_budget(self):
        opp_access = OpportunityAccess.objects.filter(opportunity=self)
        opportunity_claim = OpportunityClaim.objects.filter(opportunity_access__in=opp_access)
        claim_limits = OpportunityClaimLimit.objects.filter(opportunity_claim__in=opportunity_claim)

        payment_unit_counts = claim_limits.values("payment_unit").annotate(
            visits_count=Sum("max_visits"), amount=F("payment_unit__amount")
        )
        claimed = 0
        for count in payment_unit_counts:
            visits_count = count["visits_count"]
            amount = count["amount"]
            claimed += visits_count * amount

        return claimed

    @property
    def utilised_budget(self):
        completed_works = CompletedWork.objects.filter(opportunity_access__opportunity=self)
        payment_unit_counts = completed_works.values("payment_unit").annotate(
            completed_count=Count("id"), amount=F("payment_unit__amount")
        )
        utilised = 0
        for payment_unit_count in payment_unit_counts:
            completed_count = payment_unit_count["completed_count"]
            amount = payment_unit_count["amount"]
            utilised += completed_count * amount
        return utilised

    @property
    def claimed_visits(self):
        opp_access = OpportunityAccess.objects.filter(opportunity=self)
        opportunity_claim = OpportunityClaim.objects.filter(opportunity_access__in=opp_access)
        used_budget = OpportunityClaimLimit.objects.filter(opportunity_claim__in=opportunity_claim).aggregate(
            Sum("max_visits")
        )["max_visits__sum"]
        if used_budget is None:
            used_budget = 0
        return used_budget

    @property
    def approved_visits(self):
        return CompletedWork.objects.filter(opportunity_access__opportunity=self).count()

    @property
    def number_of_users(self):
        return self.total_budget / self.budget_per_user

    @property
    def allotted_visits(self):
        return self.max_visits_per_user_new * self.number_of_users

    @property
    def max_visits_per_user_new(self):
        return self.paymentunit_set.aggregate(max_total=Sum("max_total")).get("max_total", 0)

    @property
    def daily_max_visits_per_user_new(self):
        return self.paymentunit_set.aggregate(max_daily=Sum("max_daily")).get("max_daily", 0)

    @property
    def budget_per_visit_new(self):
        return self.paymentunit_set.aggregate(amount=Max("amount")).get("amount", 0)

    @property
    def budget_per_user(self):
        payment_units = self.paymentunit_set.all()
        budget = 0
        for pu in payment_units:
            budget += pu.max_total * pu.amount
        return budget

    @property
    def is_active(self):
        return self.active and self.end_date and self.end_date >= now().date()


class OpportunityVerificationFlags(models.Model):
    opportunity = models.OneToOneField(Opportunity, on_delete=models.CASCADE)
    duration = models.PositiveIntegerField(default=1)
    gps = models.BooleanField(default=True)
    duplicate = models.BooleanField(default=True)
    location = models.PositiveIntegerField(default=10)
    form_submission_start = models.TimeField(null=True, blank=True)
    form_submission_end = models.TimeField(null=True, blank=True)


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
    suspended = models.BooleanField(default=False)
    suspension_date = models.DateTimeField(null=True, blank=True)
    suspension_reason = models.CharField(max_length=300, null=True, blank=True)

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
        return sum(
            [cw.completed for cw in self.completedwork_set.exclude(status=CompletedWorkStatus.over_limit).all()]
        )

    @property
    def last_visit_date(self):
        user_visits = (
            UserVisit.objects.filter(user=self.user_id, opportunity=self.opportunity)
            .exclude(status__in=[VisitValidationStatus.over_limit, VisitValidationStatus.trial])
            .order_by("visit_date")
        )
        if user_visits.exists():
            return user_visits.last().visit_date
        return

    @property
    def total_paid(self):
        return Payment.objects.filter(opportunity_access=self).aggregate(total=Sum("amount")).get("total", 0) or 0

    @property
    def display_name(self):
        if self.accepted:
            return self.user.name
        else:
            return "---"

    @cached_property
    def _assessment_counts(self):
        return Assessment.objects.filter(user=self.user, opportunity=self.opportunity).aggregate(
            total=Count("pk"),
            failed=Count("pk", filter=Q(passed=False)),
            passed=Count("pk", filter=Q(passed=True)),
        )

    @property
    def assessment_count(self):
        return self._assessment_counts.get("total", 0)

    @property
    def assessment_status(self):
        assessments = self._assessment_counts
        if assessments.get("passed", 0) > 0:
            status = "Passed"
        elif assessments.get("failed", 0) > 0:
            status = "Failed"
        else:
            status = "Not completed"
        return status


class PaymentUnit(models.Model):
    opportunity = models.ForeignKey(Opportunity, on_delete=models.PROTECT)
    amount = models.PositiveIntegerField()
    name = models.CharField(max_length=255)
    description = models.TextField()
    max_total = models.IntegerField(null=True)
    max_daily = models.IntegerField(null=True)
    parent_payment_unit = models.ForeignKey(
        "self",
        on_delete=models.DO_NOTHING,
        related_name="child_payment_units",
        blank=True,
        null=True,
    )


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
        on_delete=models.DO_NOTHING,
        related_name="deliver_units",
        related_query_name="deliver_unit",
        null=True,
    )
    optional = models.BooleanField(default=False)

    def __str__(self):
        return self.name


class VisitValidationStatus(models.TextChoices):
    pending = "pending", gettext("Pending")
    approved = "approved", gettext("Approved")
    rejected = "rejected", gettext("Rejected")
    over_limit = "over_limit", gettext("Over Limit")
    duplicate = "duplicate", gettext("Duplicate")
    trial = "trial", gettext("Trial")


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
    confirmed = models.BooleanField(default=False)
    confirmation_date = models.DateTimeField(null=True)


class CompletedWorkStatus(models.TextChoices):
    pending = "pending", gettext("Pending")
    approved = "approved", gettext("Approved")
    rejected = "rejected", gettext("Rejected")
    over_limit = "over_limit", gettext("Over Limit")
    incomplete = "incomplete", gettext("Incomplete")


class CompletedWork(models.Model):
    opportunity_access = models.ForeignKey(OpportunityAccess, on_delete=models.CASCADE)
    payment_unit = models.ForeignKey(PaymentUnit, on_delete=models.DO_NOTHING)
    status = models.CharField(
        max_length=50, choices=CompletedWorkStatus.choices, default=CompletedWorkStatus.incomplete
    )
    last_modified = models.DateTimeField(auto_now=True)
    entity_id = models.CharField(max_length=255, null=True, blank=True)
    entity_name = models.CharField(max_length=255, null=True, blank=True)
    reason = models.CharField(max_length=300, null=True, blank=True)
    status_modified_date = models.DateTimeField(null=True)

    def update_status(self, status):
        self.status = status
        self.status_modified_date = now()

    # TODO: add caching on this property
    @property
    def completed_count(self):
        """Returns the no of completion of this work. Includes duplicate submissions."""
        visits = self.uservisit_set.values_list("deliver_unit_id", flat=True)
        return self.calculate_completed(visits)

    @property
    def approved_count(self):
        visits = self.uservisit_set.filter(status=VisitValidationStatus.approved).values_list(
            "deliver_unit_id", flat=True
        )
        return self.calculate_completed(visits, approved=True)

    def calculate_completed(self, visits, approved=False):
        unit_counts = Counter(visits)
        deliver_units = self.payment_unit.deliver_units.values("id", "optional")
        required_deliver_units = list(
            du["id"] for du in filter(lambda du: not du.get("optional", False), deliver_units)
        )
        optional_deliver_units = list(du["id"] for du in filter(lambda du: du.get("optional", False), deliver_units))
        # NOTE: The min unit count is the completed required deliver units for an entity_id
        number_completed = min(unit_counts[deliver_id] for deliver_id in required_deliver_units)
        if optional_deliver_units:
            # The sum calculates the number of optional deliver units completed and to process
            # duplicates with extra optional deliver units
            optional_completed = sum(unit_counts[deliver_id] for deliver_id in optional_deliver_units)
            number_completed = min(number_completed, optional_completed)
        child_payment_units = self.payment_unit.child_payment_units.all()
        if child_payment_units:
            child_completed_works = CompletedWork.objects.filter(
                opportunity_access=self.opportunity_access,
                payment_unit__in=child_payment_units,
                entity_id=self.entity_id,
            )
            child_completed_work_count = 0
            for completed_work in child_completed_works:
                if approved:
                    child_completed_work_count += completed_work.approved_count
                else:
                    child_completed_work_count += completed_work.completed_count
            number_completed = min(number_completed, child_completed_work_count)
        return number_completed

    @property
    def completed(self):
        return self.completed_count > 0

    @property
    def payment_accrued(self):
        """Returns the total payment accrued for this completed work. Includes duplicates"""
        return self.approved_count * self.payment_unit.amount

    @property
    def flags(self):
        visits = self.uservisit_set.exclude(status=VisitValidationStatus.approved).values_list(
            "flag_reason", flat=True
        )
        flags = set()
        for visit in visits:
            if not visit:
                continue
            for flag, _ in visit.get("flags", []):
                flags.add(flag)
        return list(flags)

    @property
    def completion_date(self):
        visit = self.uservisit_set.order_by("visit_date").last()
        return visit.visit_date if visit else None


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
    completed_work = models.ForeignKey(CompletedWork, on_delete=models.DO_NOTHING, null=True, blank=True)
    status_modified_date = models.DateTimeField(null=True)

    def update_status(self, status):
        self.status = status
        self.status_modified_date = now()

    @property
    def images(self):
        return BlobMeta.objects.filter(parent_id=self.xform_id, content_type__startswith="image/")


class OpportunityClaim(models.Model):
    opportunity_access = models.OneToOneField(OpportunityAccess, on_delete=models.CASCADE)
    # to be removed
    max_payments = models.IntegerField(null=True)
    end_date = models.DateField()
    date_claimed = models.DateField(auto_now_add=True)


class OpportunityClaimLimit(models.Model):
    opportunity_claim = models.ForeignKey(OpportunityClaim, on_delete=models.CASCADE)
    payment_unit = models.ForeignKey(PaymentUnit, on_delete=models.CASCADE)
    max_visits = models.IntegerField()

    class Meta:
        unique_together = [
            ("opportunity_claim", "payment_unit"),
        ]

    @classmethod
    def create_claim_limits(cls, opportunity: Opportunity, claim: OpportunityClaim):
        claim_limits_by_payment_unit = defaultdict(list)
        claim_limits = OpportunityClaimLimit.objects.filter(
            opportunity_claim__opportunity_access__opportunity=opportunity
        )
        for claim_limit in claim_limits:
            claim_limits_by_payment_unit[claim_limit.payment_unit].append(claim_limit)

        for payment_unit in opportunity.paymentunit_set.all():
            claim_limits = claim_limits_by_payment_unit.get(payment_unit, [])
            total_claimed_visits = 0
            for claim_limit in claim_limits:
                total_claimed_visits += claim_limit.max_visits

            remaining = (payment_unit.max_total) * opportunity.number_of_users - total_claimed_visits
            if remaining < 1:
                # claimed limit exceeded for this paymentunit
                continue
            OpportunityClaimLimit.objects.get_or_create(
                opportunity_claim=claim, payment_unit=payment_unit, max_visits=min(remaining, payment_unit.max_total)
            )


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


class UserInviteStatus(models.TextChoices):
    sms_delivered = "sms_delivered", gettext("SMS Delivered")
    sms_not_delivered = "sms_not_delivered", gettext("SMS Not Delivered")
    accepted = "accepted", gettext("Accepted")
    invited = "invited", gettext("Invited")
    not_found = "not_found", gettext("ConnectID Not Found")


class UserInvite(models.Model):
    opportunity = models.ForeignKey(Opportunity, on_delete=models.CASCADE)
    phone_number = models.CharField(max_length=15)
    opportunity_access = models.OneToOneField(OpportunityAccess, on_delete=models.CASCADE, null=True, blank=True)
    message_sid = models.CharField(max_length=50, null=True, blank=True)
    status = models.CharField(max_length=50, choices=UserInviteStatus.choices, default=UserInviteStatus.invited)
