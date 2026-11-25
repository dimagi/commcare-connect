"""A worker's access to an opportunity: invites, claims and claim limits."""

from decimal import Decimal
from uuid import uuid4

from django.db import models, transaction
from django.db.models import Count, Q, Sum
from django.utils.functional import cached_property
from django.utils.translation import gettext

from commcare_connect.opportunity.models.base import CHOICE_FIELD_MAX_LENGTH
from commcare_connect.opportunity.models.opportunity import Opportunity
from commcare_connect.opportunity.models.payment_units import PaymentUnit
from commcare_connect.users.models import User


class OpportunityAccess(models.Model):
    opportunity_access_id = models.UUIDField(editable=False, default=uuid4, unique=True)
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
    completed_learn_date = models.DateTimeField(null=True)
    last_active = models.DateTimeField(null=True)

    class Meta:
        indexes = [
            models.Index(fields=["invite_id"]),
            models.Index(fields=["opportunity", "date_learn_started"]),
        ]
        unique_together = ("user", "opportunity")

    # TODO: Convert to a field and calculate this property CompletedModule is saved
    @property
    def learn_progress(self):
        from commcare_connect.opportunity.models.learn import LearnModule

        learn_modules = LearnModule.objects.filter(app=self.opportunity.learn_app)
        learn_modules_count = learn_modules.count()
        if learn_modules_count <= 0:
            return 0
        completed_modules = self.unique_completed_modules.count()
        percentage = (completed_modules / learn_modules_count) * 100
        return round(percentage, 2)

    @property
    def visit_count(self):
        from commcare_connect.opportunity.models.completed_work import CompletedWorkStatus

        return (
            self.completedwork_set.exclude(status=CompletedWorkStatus.over_limit).aggregate(
                total=Sum("saved_completed_count")
            )["total"]
            or 0
        )

    @property
    def last_visit_date(self):
        from commcare_connect.opportunity.models.visits import UserVisit, VisitValidationStatus

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
        from commcare_connect.opportunity.models.invoices import Payment

        return Payment.objects.filter(opportunity_access=self).aggregate(total=Sum("amount")).get(
            "total", 0
        ) or Decimal("0.00")

    @property
    def total_confirmed_paid(self):
        from commcare_connect.opportunity.models.invoices import Payment

        return Payment.objects.filter(opportunity_access=self, confirmed=True).aggregate(total=Sum("amount")).get(
            "total", 0
        ) or Decimal("0.00")

    @property
    def display_name(self):
        if self.accepted:
            return self.user.name
        else:
            return "---"

    @cached_property
    def _assessment_counts(self):
        from commcare_connect.opportunity.models.learn import Assessment

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
            status = None
        return status

    @property
    def unique_completed_modules(self):
        return self.completedmodule_set.order_by("module", "date").distinct("module")


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
        """Allocate visit limits for a new claim out of the opportunity's remaining budget.

        Each payment unit is allocated up to its per-user ``max_total``, capped by what the
        budget left over from existing claims can pay for. The budget is drawn down as units
        are allocated, so the total committed across all claims can never exceed
        ``total_budget``.
        """
        with transaction.atomic():
            opportunity = Opportunity.objects.select_for_update().get(pk=opportunity.pk)
            remaining_budget = opportunity.remaining_budget
            # Units are funded in creation order, so when the budget runs out mid-claim it is
            # always the most recently added units that go short.
            for payment_unit in opportunity.paymentunit_set.order_by("pk"):
                max_visits = cls._max_funded_visits_for_worker(payment_unit, remaining_budget)
                if max_visits < 1:
                    # budget cannot pay for another visit of this payment unit
                    continue
                _, created = OpportunityClaimLimit.objects.get_or_create(
                    opportunity_claim=claim,
                    payment_unit=payment_unit,
                    defaults={
                        "max_visits": max_visits,
                        "end_date": payment_unit.end_date,
                    },
                )
                if created:
                    remaining_budget -= max_visits * cls._budgeted_cost_per_visit(payment_unit)

    @classmethod
    def _max_funded_visits_for_worker(cls, payment_unit, remaining_budget):
        """
        Calculates the maximum number of visits a worker can claim for the payment_unit, taking
        the opportunity's remaining budget into account.
        """
        if not payment_unit.max_total:
            return 0
        cost_per_visit = cls._budgeted_cost_per_visit(payment_unit)
        if not cost_per_visit:
            return payment_unit.max_total
        return min(payment_unit.max_total, remaining_budget // cost_per_visit)

    @staticmethod
    def _budgeted_cost_per_visit(payment_unit):
        return payment_unit.amount + payment_unit.org_amount


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
    status = models.CharField(
        max_length=CHOICE_FIELD_MAX_LENGTH, choices=UserInviteStatus, default=UserInviteStatus.invited
    )
    notification_date = models.DateTimeField(null=True)
