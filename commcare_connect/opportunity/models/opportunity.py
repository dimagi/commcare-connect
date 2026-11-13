"""The Opportunity itself and its per-opportunity configuration."""

import datetime
from functools import cached_property
from typing import NamedTuple
from uuid import uuid4

import pghistory
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Count, F, Q, Sum
from django.db.models.functions import Coalesce
from django.utils.timezone import now
from django.utils.translation import gettext_lazy

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


class UnitClaims(NamedTuple):
    visits: int
    cost: int


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
        return sum(claims.cost for claims in self._claims_by_unit().values())

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
        # A unit without a max_total gives claimants no visits (see OpportunityClaimLimit.create_claim_limits)
        return sum(
            (pu.max_total or 0) * (pu.amount + (pu.org_amount if include_org_pay else 0)) for pu in payment_units
        )

    def validate_budget_for_payment_units(self, payment_units):
        """Validate that ``total_budget`` can give every existing claimant the full per-user
        limit for ``payment_units``, raising ``ValidationError`` if it can't.

        ``payment_units`` is the complete set of units that would exist after the proposed
        change (each exposing ``max_total``/``amount``/``org_amount``; unsaved instances are
        fine).
        """
        if not self.total_budget:
            return
        claimant_count = self._claimed_worker_count()
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
    def budgeted_workers(self):
        """How many workers ``total_budget`` could pay in full, if every worker got each payment unit's
        ``max_total`` at the units' current amounts.

        Ignores who has actually claimed, so it drifts from reality once claims are made and payment
        units change. The finalize form uses it to turn a number of workers into a budget; for a count
        that reflects existing claims, use ``max_workers``. ``None`` when the payment units cost nothing,
        since then no number of workers adds up to the budget.
        """
        if not self.total_budget:
            return 0
        budget_per_worker = self.budget_per_user()
        if not budget_per_worker:
            return None
        return self.total_budget // budget_per_worker

    @property
    def max_workers(self):
        """Workers who have claimed, plus the whole workers the remaining budget can still fund. ``None``
        when the budget puts no limit on claims. Calculated once per instance, together with
        ``allotted_visits``."""
        return self._budget_capacity[0]

    @property
    def allotted_visits(self):
        """Deliveries the budget funds across all workers, as a whole number. ``None`` when the budget puts
        no limit on them. Calculated once per instance, together with ``max_workers``."""
        return self._budget_capacity[1]

    @cached_property
    def _budget_capacity(self):
        """``(max_workers, allotted_visits)``, from a single read of the payment units and claims.

        Workers who have claimed count with the visits in their claim limits. On top of them come the
        whole workers the remaining budget can still fund, each getting every unit's full ``max_total``.
        Cached, so it goes stale if the payment units, claims, budget or end date change afterwards;
        fetch the opportunity again to recalculate.
        """
        payment_units = list(self.paymentunit_set.all())
        claims_by_unit = self._claims_by_unit()
        unclaimed_workers = self._unclaimed_worker_count(payment_units, claims_by_unit)
        visits_by_unit = self._allotted_visits_by_unit(payment_units, claims_by_unit, unclaimed_workers)
        workers = None if unclaimed_workers is None else self._claimed_worker_count() + unclaimed_workers
        visits = None if visits_by_unit is None else sum(visits_by_unit.values())
        return workers, visits

    def allotted_visits_by_unit(self, payment_units=None):
        """Deliveries the budget funds for each payment unit across all workers.

        Workers who have claimed count with the limits in their ``OpportunityClaimLimit`` rows, so
        manual adjustments and limits set before a payment-unit change are kept. The unclaimed budget
        is projected at today's config: a worker can only claim while the remaining budget covers a
        full allocation (see ``ClaimOpportunityView``), so it funds whole workers, each getting every
        unit's full ``max_total``; a leftover too small for one more worker funds nothing. Once the
        opportunity has ended no one can claim, so the unclaimed budget funds nothing either.

        Units with neither a claim limit nor a ``max_total`` are left out. Empty when the units have a
        price but there is no budget, or when the units cost nothing and so have no budget limit.
        """
        if payment_units is None:
            payment_units = self.paymentunit_set.all()
        claims_by_unit = self._claims_by_unit()
        unclaimed_workers = self._unclaimed_worker_count(payment_units, claims_by_unit)
        # None (no limit) becomes {} here, the same as "priced units, no budget". Callers show both as
        # no limit. That's safe because the dashboard requires a truthy total_budget (is_setup_complete),
        # so the no-budget case never reaches them.
        return self._allotted_visits_by_unit(payment_units, claims_by_unit, unclaimed_workers) or {}

    def _allotted_visits_by_unit(self, payment_units, claims_by_unit, unclaimed_workers):
        """``allotted_visits_by_unit``, but ``None`` when the budget puts no limit on the deliveries."""
        if unclaimed_workers is None:
            # max_total is per worker, so with unlimited workers any unit that has one is unlimited too
            if any(pu.max_total for pu in payment_units):
                return None
            # No unit has a max_total, so new workers add no visits and only the existing claims count
            unclaimed_workers = 0
        elif not self.total_budget and self.budget_per_user_for_units(payment_units):
            return {}
        return {
            pu.pk: (claims_by_unit[pu.pk].visits if pu.pk in claims_by_unit else 0)
            + (pu.max_total or 0) * unclaimed_workers
            for pu in payment_units
            if pu.max_total or pu.pk in claims_by_unit
        }

    def _claimed_worker_count(self):
        from commcare_connect.opportunity.models.access import OpportunityClaim

        return OpportunityClaim.objects.filter(opportunity_access__opportunity=self).count()

    def _unclaimed_worker_count(self, payment_units, claims_by_unit):
        """Whole workers the remaining budget can still fund, or ``None`` when it puts no limit on them.

        Applies ``ClaimOpportunityView``'s rule: a worker can claim while the remaining budget covers
        ``budget_per_user()``, which is what ``budget_per_user_for_units`` prices here. That rule lets
        anyone claim when the units cost nothing, even on a budget of 0, which is what the finalize form
        sets for them.
        """
        if self.has_ended:
            return 0

        remaining_budget = (self.total_budget or 0) - sum(claims.cost for claims in claims_by_unit.values())
        budget_per_worker = self.budget_per_user_for_units(payment_units)
        if not budget_per_worker:
            return None if remaining_budget >= 0 else 0
        return max(remaining_budget, 0) // budget_per_worker

    def _claims_by_unit(self):
        from commcare_connect.opportunity.models.access import OpportunityClaimLimit

        rows = (
            OpportunityClaimLimit.objects.filter(opportunity_claim__opportunity_access__opportunity=self)
            .values("payment_unit_id")
            .annotate(
                visits=Sum("max_visits"),
                cost=Sum(F("max_visits") * (F("payment_unit__amount") + F("payment_unit__org_amount"))),
            )
            .order_by()
        )
        return {row["payment_unit_id"]: UnitClaims(row["visits"], row["cost"]) for row in rows}

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
