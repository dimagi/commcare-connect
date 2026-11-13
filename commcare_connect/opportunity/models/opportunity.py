"""The Opportunity itself and its per-opportunity configuration."""

import datetime
from uuid import uuid4

import pghistory
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Count, F, Q, Sum
from django.db.models.functions import Coalesce
from django.utils.timezone import now
from django.utils.translation import gettext, gettext_lazy

from commcare_connect.commcarehq.models import HQServer
from commcare_connect.opportunity.models.commcare_app import CommCareApp, HQApiKey
from commcare_connect.opportunity.models.reference import Country, Currency, DeliveryType
from commcare_connect.organization.models import Organization
from commcare_connect.users.models import UserCredential
from commcare_connect.utils.db import BaseModel


class OpportunityStatus(models.IntegerChoices):
    """Derived from `active` and `end_date` by the opportunity list; ordered so Active sorts first."""

    ACTIVE = 0, gettext_lazy("Active")
    ENDED = 1, gettext_lazy("Ended")
    INACTIVE = 2, gettext_lazy("Inactive")


# Tracked separately from the fields=["active"] tracker below rather than by adding
# supervising_organization to its field list: pghistory derives the event model name from
# the fields, so extending it would rename OpportunityActiveEvent, which is referred to by
# name in code.
#
# Both labels, "supervising_organization_insert" and "supervising_organization_update", are
# given explicitly. A label must be unique among a model's trackers, and the
# fields=["active"] tracker below already holds the default "update", so leaving this
# tracker's UpdateEvent to default raises ValueError. Only that update label is strictly
# required; "supervising_organization_insert" is named to match rather than left as the
# default "insert", because the label is stored as pgh_label on every event row and a bare
# "insert" beside "supervising_organization_update" would read inconsistently. It also
# keeps the default "insert" free, which would otherwise collide if another field on this
# model is tracked on insert later.
@pghistory.track(
    pghistory.InsertEvent("supervising_organization_insert"),
    pghistory.UpdateEvent("supervising_organization_update"),
    fields=["supervising_organization"],
)
@pghistory.track(pghistory.UpdateEvent(), fields=["active"])
# A third, separate tracker for the same reason as above; the labels, model_name and related_name
# are explicit so they stay unique among the trackers and the generated names stay short and stable.
@pghistory.track(
    pghistory.InsertEvent("api_key_insert"),
    pghistory.UpdateEvent("api_key_update"),
    fields=["api_key"],
    model_name="OpportunityApiKeyEvent",
    obj_field=pghistory.ObjForeignKey(related_name="api_key_events"),
)
class Opportunity(BaseModel):
    opportunity_id = models.UUIDField(editable=False, default=uuid4, unique=True)
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
    start_date = models.DateField(default=datetime.date.today)
    end_date = models.DateField(null=True)
    total_budget = models.PositiveBigIntegerField(null=True)
    api_key = models.ForeignKey(HQApiKey, on_delete=models.PROTECT, null=True)
    currency = models.ForeignKey(Currency, on_delete=models.PROTECT, null=True)
    country = models.ForeignKey(Country, on_delete=models.PROTECT, null=True)
    auto_approve_visits = models.BooleanField(default=True)
    auto_approve_payments = models.BooleanField(default=True)
    automatic_visit_verification = models.BooleanField(default=False)
    is_test = models.BooleanField(default=True)
    archived = models.BooleanField(default=False)
    delivery_type = models.ForeignKey(DeliveryType, null=True, blank=True, on_delete=models.DO_NOTHING)
    managed = models.BooleanField(default=False)
    program = models.ForeignKey("program.Program", on_delete=models.DO_NOTHING)
    supervising_organization = models.ForeignKey(
        Organization,
        on_delete=models.PROTECT,
        related_name="supervised_opportunities",
    )
    hq_server = models.ForeignKey(HQServer, on_delete=models.DO_NOTHING, null=True)

    def save(self, *args, **kwargs):
        # Form-driven creation sets this explicitly; the fallback covers programmatic
        # creation paths (API, factories, data migrations) for this non-null column.
        if not self.supervising_organization_id:
            self.supervising_organization_id = self.program.organization_id
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name

    @property
    def currency_code(self):
        if self.currency:
            return self.currency.code
        else:
            return None

    @property
    def is_setup_complete(self):
        if not (self.paymentunit_set.exists() and self.total_budget and self.start_date and self.end_date):
            return False
        for pu in self.paymentunit_set.all():
            if not pu.max_total:
                return False
        return True

    @property
    def remaining_budget(self) -> int:
        if self.total_budget is None:
            return 0
        return self.total_budget - self.claimed_budget

    @property
    def claimed_budget(self):
        payment_unit_counts = (
            self._claim_limits()
            .values("payment_unit")
            .annotate(
                visits_count=Sum("max_visits"),
                amount=F("payment_unit__amount"),
                org_amount=F("payment_unit__org_amount"),
            )
        )
        claimed = 0

        for count in payment_unit_counts:
            visits_count = count["visits_count"]
            amount = count["amount"]
            org_amount = count["org_amount"]
            claimed += visits_count * (amount + org_amount)

        return claimed

    @property
    def claimed_visits(self):
        used_budget = self._claim_limits().aggregate(Sum("max_visits"))["max_visits__sum"]
        if used_budget is None:
            used_budget = 0
        return used_budget

    def _claim_limits(self):
        from commcare_connect.opportunity.models.access import (
            OpportunityAccess,
            OpportunityClaim,
            OpportunityClaimLimit,
        )

        opp_access = OpportunityAccess.objects.filter(opportunity=self)
        opportunity_claim = OpportunityClaim.objects.filter(opportunity_access__in=opp_access)
        return OpportunityClaimLimit.objects.filter(opportunity_claim__in=opportunity_claim)

    @property
    def approved_visits(self):
        from commcare_connect.opportunity.models.completed_work import CompletedWork, CompletedWorkStatus

        return CompletedWork.objects.filter(
            opportunity_access__opportunity=self, status=CompletedWorkStatus.approved
        ).count()

    @staticmethod
    def budget_per_user_for_units(payment_units, include_org_pay=True):
        """
        Budget consumed per user across the given payment units; includes org pay unless
        include_org_pay is False.
        """
        return sum(pu.max_total * (pu.amount + (pu.org_amount if include_org_pay else 0)) for pu in payment_units)

    def validate_payment_unit_dates(self, unit_start_date, unit_end_date):
        errors = {}
        if unit_start_date:
            if unit_start_date < self.start_date:
                message = gettext("Start date can't be before the opportunity's start date (%(date)s).")
                errors["start_date"] = message % {"date": self.start_date}
            elif self.end_date and unit_start_date > self.end_date:
                message = gettext("Start date can't be after the opportunity's end date (%(date)s).")
                errors["start_date"] = message % {"date": self.end_date}
        if unit_end_date:
            if self.end_date and unit_end_date > self.end_date:
                message = gettext("End date can't be after the opportunity's end date (%(date)s).")
                errors["end_date"] = message % {"date": self.end_date}
            elif unit_end_date < self.start_date:
                message = gettext("End date can't be before the opportunity's start date (%(date)s).")
                errors["end_date"] = message % {"date": self.start_date}
        if errors:
            raise ValidationError(errors)

    def has_payable_visits(self):
        from commcare_connect.opportunity.models.visits import UserVisit, VisitValidationStatus

        # Trial visits get no completed work, so they are never paid
        return UserVisit.objects.filter(opportunity=self).exclude(status=VisitValidationStatus.trial).exists()

    def validate_budget_for_payment_units(self, payment_units):
        """Validate that ``total_budget`` can give every existing claimant the full per-user
        limit for ``payment_units``, raising ``ValidationError`` if it can't.

        ``payment_units`` is the complete set of units that would exist after the proposed
        change (each exposing ``max_total``/``amount``/``org_amount``; unsaved instances are
        fine).
        """
        from commcare_connect.opportunity.models.access import OpportunityClaim

        if not self.total_budget:
            return
        claimant_count = OpportunityClaim.objects.filter(opportunity_access__opportunity=self).count()
        if not claimant_count:
            return

        required_budget = self.budget_per_user_for_units(payment_units) * claimant_count
        if self.total_budget < required_budget:
            raise ValidationError(
                gettext_lazy(
                    "The opportunity budget cannot give the full limit to all %(claimants)d workers "
                    "who have already claimed. Increase it to at least %(required)d %(currency)s."
                )
                % {
                    "claimants": claimant_count,
                    "required": required_budget,
                    "currency": self.currency_code or "",
                }
            )

    @property
    def number_of_users(self):
        if not self.total_budget:
            return 0
        return self.total_budget / self.budget_per_user()

    @property
    def allotted_visits(self):
        return self.max_visits_per_user * self.number_of_users

    @property
    def max_visits_per_user(self):
        # aggregates return None
        return self.paymentunit_set.aggregate(max_total=Sum("max_total")).get("max_total", 0) or 0

    @property
    def daily_max_visits_per_user(self):
        # API 1.0: a unit without a daily limit can do at most max_total visits in a day
        return (
            self.paymentunit_set.aggregate(max_daily=Sum(Coalesce("max_daily", "max_total"))).get("max_daily", 0) or 0
        )

    @property
    def daily_max_visits_per_user_v2(self):
        # API 2.0: a unit without a daily limit leaves the opportunity without one
        limits = self.paymentunit_set.aggregate(
            daily_total=Sum("max_daily"), uncapped=Count("pk", filter=Q(max_daily__isnull=True))
        )
        if limits["uncapped"]:
            return None
        return limits["daily_total"]

    @property
    def budget_per_visit(self):
        return self.paymentunit_set.aggregate(amount=Sum("amount")).get("amount", 0) or 0

    def budget_per_user(self, include_org_pay=True):
        return self.budget_per_user_for_units(
            self.paymentunit_set.all(),
            include_org_pay=include_org_pay,
        )

    @property
    def is_active(self):
        return bool(not self.archived and self.active and self.end_date and self.end_date >= now().date())

    @property
    def has_ended(self):
        return bool(self.end_date and self.end_date < now().date())


class CredentialConfiguration(models.Model):
    opportunity = models.ForeignKey(
        Opportunity,
        unique=True,
        on_delete=models.CASCADE,
    )
    learn_level = models.CharField(
        null=True,
        blank=True,
        max_length=32,
        choices=UserCredential.LearnLevel.choices,
    )
    delivery_level = models.CharField(
        null=True,
        blank=True,
        max_length=32,
        choices=UserCredential.DeliveryLevel.choices,
    )
