import datetime
from collections import Counter, defaultdict
from uuid import uuid4

from django.conf import settings
from django.db import models
from django.db.models import Count, F, Max, Q, Sum
from django.utils.dateparse import parse_datetime
from django.utils.functional import cached_property
from django.utils.timezone import now
from django.utils.translation import gettext

from commcare_connect.organization.models import Organization
from commcare_connect.users.models import User
from commcare_connect.utils.db import BaseModel, slugify_uniquely


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
    total_budget = models.PositiveBigIntegerField(null=True)
    api_key = models.ForeignKey(HQApiKey, on_delete=models.DO_NOTHING, null=True)
    currency = models.CharField(max_length=3, null=True)
    auto_approve_visits = models.BooleanField(default=True)
    auto_approve_payments = models.BooleanField(default=True)
    is_test = models.BooleanField(default=True)
    delivery_type = models.ForeignKey(DeliveryType, null=True, blank=True, on_delete=models.DO_NOTHING)
    managed = models.BooleanField(default=False)

    def __str__(self):
        return self.name

    @property
    def org_pay_per_visit(self):
        return self.managedopportunity.org_pay_per_visit if self.managed else 0

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
        org_pay = self.org_pay_per_visit

        payment_unit_counts = claim_limits.values("payment_unit").annotate(
            visits_count=Sum("max_visits"), amount=F("payment_unit__amount")
        )
        claimed = 0
        for count in payment_unit_counts:
            visits_count = count["visits_count"]
            amount = count["amount"]
            claimed += visits_count * (amount + org_pay)

        return claimed

    @property
    def utilised_budget(self):
        completed_works = CompletedWork.objects.filter(opportunity_access__opportunity=self)
        org_pay = self.org_pay_per_visit
        return sum(cw.saved_payment_accrued + org_pay for cw in completed_works)

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
        return CompletedWork.objects.filter(
            opportunity_access__opportunity=self, status=CompletedWorkStatus.approved
        ).count()

    @property
    def number_of_users(self):
        if not self.managed:
            return self.total_budget / self.budget_per_user

        budget_per_user = 0
        payment_units = self.paymentunit_set.all()
        org_pay = self.org_pay_per_visit
        for pu in payment_units:
            budget_per_user += pu.max_total * (pu.amount + org_pay)

        return self.total_budget / budget_per_user

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
        return bool(self.active and self.end_date and self.end_date >= now().date())


class OpportunityVerificationFlags(models.Model):
    opportunity = models.OneToOneField(Opportunity, on_delete=models.CASCADE)
    duration = models.PositiveIntegerField(default=1)
    gps = models.BooleanField(default=True)
    duplicate = models.BooleanField(default=True)
    location = models.PositiveIntegerField(default=10)
    form_submission_start = models.TimeField(null=True, blank=True)
    form_submission_end = models.TimeField(null=True, blank=True)
    catchment_areas = models.BooleanField(default=False)


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
    invited_date = models.DateTimeField(auto_now_add=True, editable=False, null=True)

    class Meta:
        indexes = [models.Index(fields=["invite_id"])]
        unique_together = ("user", "opportunity")

    @cached_property
    def managed_opportunity(self):
        from commcare_connect.program.models import ManagedOpportunity

        if self.opportunity.managed:
            return ManagedOpportunity.objects.get(id=self.opportunity.id)

        return None

    # TODO: Convert to a field and calculate this property CompletedModule is saved
    @property
    def learn_progress(self):
        learn_modules = LearnModule.objects.filter(app=self.opportunity.learn_app)
        learn_modules_count = learn_modules.count()
        if learn_modules_count <= 0:
            return 0
        completed_modules = self.unique_completed_modules.count()
        percentage = (completed_modules / learn_modules_count) * 100
        return round(percentage, 2)

    @property
    def visit_count(self):
        return (
            self.completedwork_set.exclude(status=CompletedWorkStatus.over_limit).aggregate(
                total=Sum("saved_completed_count")
            )["total"]
            or 0
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
    def total_confirmed_paid(self):
        return (
            Payment.objects.filter(opportunity_access=self, confirmed=True)
            .aggregate(total=Sum("amount"))
            .get("total", 0)
            or 0
        )

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

    @property
    def unique_completed_modules(self):
        return self.completedmodule_set.order_by("module", "date").distinct("module")


class CompletedModule(XFormBaseModel):
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="completed_modules",
    )
    module = models.ForeignKey(LearnModule, on_delete=models.PROTECT)
    opportunity = models.ForeignKey(Opportunity, on_delete=models.PROTECT)
    opportunity_access = models.ForeignKey(OpportunityAccess, on_delete=models.CASCADE, null=True)
    date = models.DateTimeField()
    duration = models.DurationField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["xform_id", "module", "opportunity_access"], name="unique_xform_completed_module"
            )
        ]


class Assessment(XFormBaseModel):
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="assessments",
    )
    app = models.ForeignKey(CommCareApp, on_delete=models.PROTECT)
    opportunity = models.ForeignKey(Opportunity, on_delete=models.PROTECT)
    opportunity_access = models.ForeignKey(OpportunityAccess, on_delete=models.CASCADE, null=True)
    date = models.DateTimeField()
    score = models.IntegerField()
    passing_score = models.IntegerField()
    passed = models.BooleanField()


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
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)

    def __str__(self):
        return self.name


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


class PaymentInvoice(models.Model):
    opportunity = models.ForeignKey(Opportunity, on_delete=models.CASCADE)
    amount = models.PositiveIntegerField()
    date = models.DateField()
    invoice_number = models.CharField(max_length=50)
    service_delivery = models.BooleanField(default=True)

    class Meta:
        unique_together = ("opportunity", "invoice_number")


class Payment(models.Model):
    amount = models.PositiveIntegerField()
    amount_usd = models.DecimalField(max_digits=10, decimal_places=2, null=True)
    date_paid = models.DateTimeField(default=datetime.datetime.utcnow)
    # This is used to indicate payments made to Opportunity Users
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
    # This is used to indicate Payments made to Network Manager organizations
    organization = models.ForeignKey(Organization, on_delete=models.DO_NOTHING, null=True, blank=True)
    invoice = models.OneToOneField(PaymentInvoice, on_delete=models.DO_NOTHING, null=True, blank=True)
    payment_method = models.CharField(max_length=50, null=True, blank=True)
    payment_operator = models.CharField(max_length=50, null=True, blank=True)


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
    payment_date = models.DateTimeField(null=True)
    date_created = models.DateTimeField(auto_now_add=True)

    # these fields are the stored/cached versions of the completed_count and approved_count
    # and the associated calculations needed to do reporting on payments.
    # it is expected that they are updated every time the completed_count or approved_count is updated,
    # but should not be used for real-time display of that information until confirmed to be working.
    saved_completed_count = models.IntegerField(default=0)
    saved_approved_count = models.IntegerField(default=0)
    saved_payment_accrued = models.IntegerField(default=0, help_text="Payment accrued for the FLW.")
    saved_payment_accrued_usd = models.DecimalField(
        max_digits=10, decimal_places=2, default=0, help_text="Payment accrued for the FLW in USD."
    )
    saved_org_payment_accrued = models.IntegerField(default=0, help_text="Payment accrued for the organization")
    saved_org_payment_accrued_usd = models.DecimalField(
        max_digits=10, decimal_places=2, default=0, help_text="Payment accrued for the organization in USD."
    )

    class Meta:
        unique_together = ("opportunity_access", "entity_id", "payment_unit")

    def __init__(self, *args, **kwargs):
        self.status = CompletedWorkStatus.incomplete
        self.status_modified_date = now()
        super().__init__(*args, **kwargs)

    def __setattr__(self, name, value):
        if name == "status":
            if getattr(self, "status", None) != value:  # Check if status has changed
                self.status_modified_date = now()
        super().__setattr__(name, value)

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
        if required_deliver_units:
            number_completed = min(unit_counts[deliver_id] for deliver_id in required_deliver_units)
        else:
            # this is an unexpected case, but can show up in old/test data
            number_completed = 0
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


class VisitReviewStatus(models.TextChoices):
    pending = "pending", gettext("Pending Review")
    agree = "agree", gettext("Agree")
    disagree = "disagree", gettext("Disagree")


class UserVisit(XFormBaseModel):
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
        max_length=50, choices=VisitValidationStatus.choices, default=VisitValidationStatus.pending
    )
    form_json = models.JSONField()
    reason = models.CharField(max_length=300, null=True, blank=True)
    location = models.CharField(null=True)
    flagged = models.BooleanField(default=False)
    flag_reason = models.JSONField(null=True, blank=True)
    completed_work = models.ForeignKey(CompletedWork, on_delete=models.DO_NOTHING, null=True, blank=True)
    status_modified_date = models.DateTimeField(null=True)
    review_status = models.CharField(
        max_length=50, choices=VisitReviewStatus.choices, default=VisitReviewStatus.pending
    )
    review_created_on = models.DateTimeField(blank=True, null=True)
    justification = models.CharField(max_length=300, null=True, blank=True)
    date_created = models.DateTimeField(auto_now_add=True)

    def __init__(self, *args, **kwargs):
        self.status = VisitValidationStatus.pending
        self.status_modified_date = now()
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
    def duration(self):
        duration = None
        start = self.form_json["metadata"].get("timeStart")
        end = self.form_json["metatdata"].get("timeEnd")
        if start and end:
            try:
                duration = parse_datetime(end) - parse_datetime(start)
            except (TypeError, ValueError):
                pass
        return duration

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["xform_id", "entity_id", "deliver_unit"], name="unique_xform_entity_deliver_unit"
            )
        ]


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
    end_date = models.DateField(null=True, blank=True)

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
                opportunity_claim=claim,
                payment_unit=payment_unit,
                defaults={"max_visits": min(remaining, payment_unit.max_total)},
                end_date=payment_unit.end_date,
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
    notification_date = models.DateTimeField(null=True)


class FormJsonValidationRules(models.Model):
    slug = models.SlugField()
    name = models.CharField(max_length=25)
    deliver_unit = models.ManyToManyField(DeliverUnit)
    opportunity = models.ForeignKey(Opportunity, on_delete=models.CASCADE)
    question_path = models.CharField(max_length=255)
    question_value = models.CharField(max_length=255)

    def save(self, *args, **kwargs):
        if not self.id:
            self.slug = slugify_uniquely(self.name, self.__class__)
        super().save(*args, **kwargs)


class DeliverUnitFlagRules(models.Model):
    deliver_unit = models.ForeignKey(DeliverUnit, on_delete=models.CASCADE)
    opportunity = models.ForeignKey(Opportunity, on_delete=models.CASCADE)
    check_attachments = models.BooleanField(default=False)
    duration = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = ("deliver_unit", "opportunity")


class CatchmentArea(models.Model):
    opportunity = models.ForeignKey(Opportunity, on_delete=models.CASCADE)
    latitude = models.DecimalField(max_digits=11, decimal_places=8)
    longitude = models.DecimalField(max_digits=11, decimal_places=8)
    radius = models.IntegerField(default=1000)
    opportunity_access = models.ForeignKey(OpportunityAccess, null=True, on_delete=models.DO_NOTHING)
    active = models.BooleanField(default=True)
    name = models.CharField(max_length=255)
    site_code = models.SlugField(max_length=255)

    class Meta:
        unique_together = ("site_code", "opportunity")


class ExchangeRate(models.Model):
    currency_code = models.CharField(max_length=3)
    rate = models.DecimalField(max_digits=10, decimal_places=6)
    rate_date = models.DateField()
    fetched_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["currency_code", "rate_date"], name="unique_currency_code_date")
        ]
