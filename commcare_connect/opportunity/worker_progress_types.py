"""Dataclasses the opportunity dashboard's Delivery Allocation and Payments to Connect Workers cards render."""

from dataclasses import dataclass

from django.contrib.humanize.templatetags.humanize import intcomma

from commcare_connect.opportunity.helpers import amount_with_currency

# A limit is "nearly reached" once this share of it or less remains
NEARING_LIMIT_FRACTION = 0.1


@dataclass
class StatusCounts:
    approved: int = 0
    pending: int = 0
    rejected: int = 0

    @property
    def completed(self):
        # All three count toward a worker's limit, so all three count toward the totals
        return self.approved + self.pending + self.rejected

    def __add__(self, other):
        return StatusCounts(
            approved=self.approved + other.approved,
            pending=self.pending + other.pending,
            rejected=self.rejected + other.rejected,
        )


@dataclass
class LimitUsage:
    """Completed deliveries against a delivery limit. ``limit`` is None when the opportunity has no budget."""

    counts: StatusCounts
    limit: int | None

    @property
    def completed(self):
        return self.counts.completed

    @property
    def completed_label(self):
        limit = "--" if self.limit is None else intcomma(self.limit)
        return f"{intcomma(self.completed)}/{limit}"

    @property
    def remaining(self):
        if self.limit is None:
            return None
        return max(self.limit - self.completed, 0)

    @property
    def is_reached(self):
        return self.limit is not None and self.completed >= self.limit

    @property
    def is_exceeded(self):
        return self.limit is not None and self.completed > self.limit

    @property
    def is_nearing(self):
        return not self.is_reached and self.limit is not None and self.remaining <= self.limit * NEARING_LIMIT_FRACTION

    @property
    def completed_width(self):
        return _bar_width(self.completed, self.limit)

    @property
    def approved_width(self):
        return _bar_width(self.counts.approved, self._split_scale)

    @property
    def pending_width(self):
        return _bar_width(self.counts.pending, self._split_scale)

    @property
    def rejected_width(self):
        return _bar_width(self.counts.rejected, self._split_scale)

    @property
    def _split_scale(self):
        # Past the limit the split bar is scaled to what was completed, so it stays within the track
        return max(self.limit or 0, self.completed)


@dataclass
class DailyUsage:
    """Deliveries today for one payment unit against the combined daily capacity of all active workers."""

    delivered: int
    capacity: int

    @property
    def label(self):
        return f"{intcomma(self.delivered)}/{intcomma(self.capacity)}"

    @property
    def is_reached(self):
        return self.delivered > 0 and self.delivered >= self.capacity

    @property
    def width(self):
        return _bar_width(self.delivered, self.capacity) if self.capacity else (100 if self.delivered else 0)


@dataclass
class PaymentUnitUsage(LimitUsage):
    name: str = ""
    today: DailyUsage | None = None


@dataclass
class DeliveryAllocation:
    total: LimitUsage
    payment_units: list[PaymentUnitUsage]
    active_workers: int
    synced_workers: int

    @property
    def hide_pending(self):
        return self.total.counts.pending == 0


@dataclass
class WorkerPayments:
    currency: str
    budget: int | None
    worker_earned: int
    org_earned: int
    paid: int
    has_org_pay: bool

    @property
    def earned(self):
        return self.worker_earned + self.org_earned

    @property
    def worker_earned_label(self):
        return amount_with_currency(self.worker_earned, self.currency)

    @property
    def org_earned_label(self):
        return amount_with_currency(self.org_earned, self.currency)

    @property
    def paid_label(self):
        return amount_with_currency(self.paid, self.currency)

    @property
    def budget_label(self):
        return intcomma(self.budget) if self.budget else "--"

    @property
    def over_budget_label(self):
        return amount_with_currency(self.over_budget, self.currency)

    @property
    def earned_percent(self):
        if not self.budget:
            return None
        return self.earned / self.budget * 100

    @property
    def over_budget(self):
        if not self.budget:
            return 0
        return max(self.earned - self.budget, 0)

    @property
    def worker_width(self):
        return _bar_width(self.worker_earned, self._earned_scale)

    @property
    def org_width(self):
        return _bar_width(self.org_earned, self._earned_scale)

    @property
    def paid_percent(self):
        return self.paid / self.worker_earned * 100 if self.worker_earned else 0

    @property
    def paid_width(self):
        return _bar_width(self.paid, max(self.worker_earned, self.paid))

    @property
    def _earned_scale(self):
        return max(self.budget or 0, self.earned)


def _bar_width(value, scale):
    """Percentage of the track a bar segment fills, stopping at full."""
    if not scale:
        return 0
    return min(value / scale, 1) * 100
