from datetime import timedelta

import pytest
from django.template.loader import render_to_string
from django.utils.timezone import localdate, now

from commcare_connect.opportunity.helpers import amount_with_currency
from commcare_connect.opportunity.models import CompletedWorkStatus, UserVisit, VisitValidationStatus
from commcare_connect.opportunity.tests.factories import (
    CompletedWorkFactory,
    DeliverUnitFactory,
    OpportunityAccessFactory,
    OpportunityClaimFactory,
    OpportunityClaimLimitFactory,
    OpportunityFactory,
    PaymentFactory,
    PaymentUnitFactory,
    UserVisitFactory,
)
from commcare_connect.opportunity.worker_progress import (
    get_delivery_allocation,
    get_payment_unit_breakdown,
    get_worker_payments,
)
from commcare_connect.opportunity.worker_progress_types import (
    DailyUsage,
    DeliveryAllocation,
    LimitUsage,
    StatusCounts,
    WorkerPayments,
)


def _usage(completed, limit):
    return LimitUsage(counts=StatusCounts(approved=completed), limit=limit)


@pytest.mark.parametrize(
    "completed, limit, remaining, nearing, reached, exceeded",
    [
        (578, 1000, 422, False, False, False),
        (900, 1000, 100, True, False, False),
        (1000, 1000, 0, False, True, False),
        (1050, 1000, 0, False, True, True),
        (5, None, None, False, False, False),
    ],
)
def test_limit_usage_states(completed, limit, remaining, nearing, reached, exceeded):
    usage = _usage(completed, limit)
    assert usage.remaining == remaining
    assert usage.is_nearing is nearing
    assert usage.is_reached is reached
    assert usage.is_exceeded is exceeded


@pytest.mark.parametrize(
    "completed, limit, label",
    [(578, 1000, "578/1,000"), (1050, 1000, "1,050/1,000"), (5, None, "5/--")],
)
def test_limit_usage_completed_label(completed, limit, label):
    assert _usage(completed, limit).completed_label == label


def test_limit_usage_bars_stop_at_full():
    usage = LimitUsage(counts=StatusCounts(approved=1005, pending=25, rejected=20), limit=1000)
    assert usage.completed_width == 100
    # past the limit the split is scaled to the completed count, so it fills the track exactly
    assert usage.approved_width + usage.pending_width + usage.rejected_width == pytest.approx(100)


@pytest.mark.parametrize(
    "used, capacity, reached, width",
    [
        (84, 130, False, 84 / 130 * 100),
        (130, 130, True, 100),
        (140, 130, True, 100),
        (0, 0, False, 0),
        # Visits from a worker whose limit for the unit was ended after they visited, so it adds no capacity
        (2, 0, True, 100),
    ],
)
def test_daily_usage(used, capacity, reached, width):
    today = DailyUsage(used=used, capacity=capacity)
    assert today.is_reached is reached
    assert today.width == pytest.approx(width)


@pytest.mark.parametrize(
    "budget, worker, org, percent, over_budget",
    [
        (13_867_000, 6_564_600, 660_107, 52.1, 0),
        (13_867_000, 12_663_000, 1_273_335, 100.5, 69_335),
        (None, 100, 0, None, 0),
    ],
)
def test_worker_payments_earned(budget, worker, org, percent, over_budget):
    payments = WorkerPayments(
        currency="NGN", budget=budget, worker_earned=worker, org_earned=org, paid=0, has_org_pay=bool(org)
    )
    assert payments.earned_percent == (pytest.approx(percent, abs=0.1) if percent else None)
    assert payments.over_budget == over_budget
    assert payments.worker_width + payments.org_width <= 100.0001


@pytest.mark.parametrize(
    "worker, paid, percent, width",
    [
        (300, 100, 100 / 3, 100 / 3),
        (0, 0, 0, 0),
        # Paid ahead of what is earned: the bar stops at full
        (100, 150, 150, 100),
    ],
)
def test_worker_payments_paid(worker, paid, percent, width):
    payments = WorkerPayments(
        currency="NGN", budget=None, worker_earned=worker, org_earned=0, paid=paid, has_org_pay=False
    )
    assert payments.paid_percent == pytest.approx(percent)
    assert payments.paid_width == pytest.approx(width)


@pytest.mark.parametrize(
    "currency, budget, budget_label, over_budget_label",
    [
        ("NGN", 13_000_000, "13,000,000", "NGN 1,500,000"),
        ("", None, "--", "0"),
    ],
)
def test_worker_payments_labels(currency, budget, budget_label, over_budget_label):
    payments = WorkerPayments(
        currency=currency,
        budget=budget,
        worker_earned=12_000_000,
        org_earned=2_500_000,
        paid=1_234_567,
        has_org_pay=True,
    )
    prefix = f"{currency} " if currency else ""
    assert payments.worker_earned_label == f"{prefix}12,000,000"
    assert payments.org_earned_label == f"{prefix}2,500,000"
    assert payments.paid_label == f"{prefix}1,234,567"
    assert payments.budget_label == budget_label
    assert payments.over_budget_label == over_budget_label


@pytest.mark.django_db
def test_get_delivery_allocation(opportunity):
    opportunity.total_budget = 1100
    opportunity.save()
    # 50 + 50 per worker
    household = PaymentUnitFactory(opportunity=opportunity, max_total=10, max_daily=3, amount=5, org_amount=0)
    anc = PaymentUnitFactory(opportunity=opportunity, max_total=5, max_daily=None, amount=8, org_amount=2)
    household_du = DeliverUnitFactory(app=opportunity.deliver_app, payment_unit=household)

    active = OpportunityAccessFactory(opportunity=opportunity)
    active_claim = OpportunityClaimFactory(opportunity_access=active, end_date=localdate())
    OpportunityClaimLimitFactory(opportunity_claim=active_claim, payment_unit=household, max_visits=10, end_date=None)
    idle = OpportunityAccessFactory(opportunity=opportunity)
    idle_claim = OpportunityClaimFactory(opportunity_access=idle, end_date=localdate() + timedelta(days=5))
    # The unit has ended for this worker, so they add nothing to its daily capacity
    OpportunityClaimLimitFactory(
        opportunity_claim=idle_claim, payment_unit=household, max_visits=10, end_date=localdate() - timedelta(days=1)
    )
    suspended = OpportunityAccessFactory(opportunity=opportunity, suspended=True)
    suspended_claim = OpportunityClaimFactory(opportunity_access=suspended, end_date=localdate())
    OpportunityClaimLimitFactory(
        opportunity_claim=suspended_claim, payment_unit=household, max_visits=10, end_date=None
    )
    ended = OpportunityAccessFactory(opportunity=opportunity)
    OpportunityClaimFactory(opportunity_access=ended, end_date=localdate() - timedelta(days=1))

    for status in [CompletedWorkStatus.approved, CompletedWorkStatus.pending, CompletedWorkStatus.rejected]:
        CompletedWorkFactory(opportunity_access=active, payment_unit=household, status=status, saved_approved_count=2)
    # Over-limit and incomplete work do not count toward the limit
    CompletedWorkFactory(opportunity_access=active, payment_unit=anc, status=CompletedWorkStatus.over_limit)
    CompletedWorkFactory(opportunity_access=active, payment_unit=anc, status=CompletedWorkStatus.incomplete)

    cw = CompletedWorkFactory(opportunity_access=active, payment_unit=household, status=CompletedWorkStatus.pending)
    for status in [VisitValidationStatus.pending, VisitValidationStatus.approved, VisitValidationStatus.over_limit]:
        UserVisitFactory(
            opportunity=opportunity,
            opportunity_access=active,
            deliver_unit=household_du,
            completed_work=cw,
            status=status,
            visit_date=now(),
            date_created=now(),
        )

    allocation = get_delivery_allocation(opportunity)
    breakdown = get_payment_unit_breakdown(opportunity)

    by_name = {unit.name: unit for unit in breakdown.payment_units}
    # Claimants hold 30 household visits (150), leaving 950: 9 more full workers, and the leftover 50 is
    # short of a full allocation
    assert by_name[household.name].limit == 30 + 90
    assert by_name[anc.name].limit == 45
    assert allocation.total.limit == 165
    assert by_name[household.name].counts == StatusCounts(approved=2, pending=2, rejected=1)
    assert by_name[anc.name].counts == StatusCounts()
    assert allocation.total.counts == StatusCounts(approved=2, pending=2, rejected=1)
    assert allocation.active_workers == 2
    assert allocation.synced_workers == 1
    assert by_name[household.name].today == DailyUsage(used=2, capacity=3)
    assert by_name[anc.name].today is None
    assert allocation.hide_pending is False
    assert breakdown.hide_pending is False
    assert allocation.has_payment_units is True


TODAY = 0
YESTERDAY = -1
V = VisitValidationStatus
A, B, OPTIONAL_1, OPTIONAL_2 = "a", "b", "optional_1", "optional_2"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "visits, used",
    [
        # The busiest deliver unit is the one the daily limit stops first
        ([(A, TODAY, V.pending), (A, TODAY, V.pending), (B, TODAY, V.pending)], 2),
        # Every visit the form receiver counts toward the limit counts, whatever its review status
        ([(A, TODAY, V.approved), (A, TODAY, V.rejected), (A, TODAY, V.duplicate)], 3),
        ([(A, YESTERDAY, V.pending), (A, YESTERDAY, V.pending), (B, TODAY, V.pending)], 1),
        # Over-limit and trial visits don't count toward the daily limit
        ([(A, TODAY, V.pending), (A, TODAY, V.over_limit), (A, TODAY, V.trial)], 1),
        # A delivery needs a visit to only one optional deliver unit, so the least busy one is what the
        # limit stops
        ([(A, TODAY, V.pending), (OPTIONAL_1, TODAY, V.pending), (OPTIONAL_1, TODAY, V.pending)], 1),
        (
            [
                (A, TODAY, V.pending),
                (OPTIONAL_1, TODAY, V.pending),
                (OPTIONAL_1, TODAY, V.pending),
                (OPTIONAL_2, TODAY, V.pending),
                (OPTIONAL_2, TODAY, V.pending),
            ],
            2,
        ),
    ],
)
def test_daily_limit_used(opportunity, visits, used):
    unit = PaymentUnitFactory(opportunity=opportunity, max_total=10, max_daily=3)
    deliver_units = {
        A: DeliverUnitFactory(app=opportunity.deliver_app, payment_unit=unit),
        B: DeliverUnitFactory(app=opportunity.deliver_app, payment_unit=unit),
        OPTIONAL_1: DeliverUnitFactory(app=opportunity.deliver_app, payment_unit=unit, optional=True),
        OPTIONAL_2: DeliverUnitFactory(app=opportunity.deliver_app, payment_unit=unit, optional=True),
    }
    access = _claimed_worker(opportunity)
    for du, days, status in visits:
        _visit(access, deliver_units[du], status, days)

    (allocation_unit,) = get_payment_unit_breakdown(opportunity).payment_units

    assert allocation_unit.today.used == used


@pytest.mark.django_db
def test_daily_limit_used_sums_active_workers(opportunity):
    unit = PaymentUnitFactory(opportunity=opportunity, max_total=10, max_daily=3)
    a = DeliverUnitFactory(app=opportunity.deliver_app, payment_unit=unit)
    b = DeliverUnitFactory(app=opportunity.deliver_app, payment_unit=unit)
    # Each worker is stopped by a different deliver unit, and both count
    first, second = _claimed_worker(opportunity), _claimed_worker(opportunity)
    for deliver_unit in [a, a, b]:
        _visit(first, deliver_unit, V.pending)
    for deliver_unit in [b, b]:
        _visit(second, deliver_unit, V.pending)
    # Only active workers make up the daily capacity, so only their visits count
    _visit(_claimed_worker(opportunity, suspended=True), a, V.rejected)

    (allocation_unit,) = get_payment_unit_breakdown(opportunity).payment_units

    assert allocation_unit.today.used == 2 + 2


def _claimed_worker(opportunity, suspended=False):
    access = OpportunityAccessFactory(opportunity=opportunity, suspended=suspended)
    OpportunityClaimFactory(opportunity_access=access, end_date=localdate())
    return access


def _visit(access, deliver_unit, status, days=TODAY):
    UserVisitFactory(
        opportunity=access.opportunity,
        opportunity_access=access,
        deliver_unit=deliver_unit,
        status=status,
        visit_date=now() + timedelta(days=days),
    )


NO_LIMIT = object()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "limit_end_date, capacity",
    [
        (None, 3),
        # The unit is still open to the worker on its last day
        (localdate(), 3),
        (localdate() - timedelta(days=1), 0),
        # The budget ran out before the worker was funded for the unit
        (NO_LIMIT, 0),
    ],
)
def test_daily_capacity(opportunity, limit_end_date, capacity):
    unit = PaymentUnitFactory(opportunity=opportunity, max_total=10, max_daily=3)
    access = OpportunityAccessFactory(opportunity=opportunity)
    claim = OpportunityClaimFactory(opportunity_access=access, end_date=localdate())
    if limit_end_date is not NO_LIMIT:
        OpportunityClaimLimitFactory(opportunity_claim=claim, payment_unit=unit, end_date=limit_end_date)

    (allocation_unit,) = get_payment_unit_breakdown(opportunity).payment_units

    assert allocation_unit.today.capacity == capacity


@pytest.mark.django_db
@pytest.mark.parametrize("hours_ago, synced", [(1, 1), (25, 0)])
def test_synced_workers(opportunity, hours_ago, synced):
    access = OpportunityAccessFactory(opportunity=opportunity)
    OpportunityClaimFactory(opportunity_access=access, end_date=localdate())
    visit = UserVisitFactory(opportunity=opportunity, opportunity_access=access)
    # date_created is auto_now_add, so it can only be backdated after the visit is saved
    UserVisit.objects.filter(pk=visit.pk).update(date_created=now() - timedelta(hours=hours_ago))

    allocation = get_delivery_allocation(opportunity)

    assert allocation.active_workers == 1
    assert allocation.synced_workers == synced


@pytest.mark.django_db
def test_other_opportunities_are_left_out(opportunity):
    other = OpportunityFactory()
    unit = PaymentUnitFactory(opportunity=other, org_amount=2)
    access = OpportunityAccessFactory(opportunity=other, payment_accrued=300)
    OpportunityClaimFactory(opportunity_access=access, end_date=localdate())
    PaymentFactory(opportunity_access=access, amount=100)
    CompletedWorkFactory(
        opportunity_access=access, payment_unit=unit, status=CompletedWorkStatus.approved, saved_org_payment_accrued=20
    )
    UserVisitFactory(opportunity=other, opportunity_access=access)

    allocation = get_delivery_allocation(opportunity)
    payments = get_worker_payments(opportunity)

    assert allocation.active_workers == 0
    assert allocation.synced_workers == 0
    assert payments.worker_earned == 0
    assert payments.org_earned == 0
    assert payments.paid == 0
    assert payments.has_org_pay is False


@pytest.mark.django_db
def test_get_delivery_allocation_without_budget(opportunity):
    opportunity.total_budget = None
    opportunity.save()
    PaymentUnitFactory(opportunity=opportunity)

    allocation = get_delivery_allocation(opportunity)

    assert allocation.total.limit is None
    assert allocation.total.completed_label == "0/--"
    assert allocation.hide_pending is True


@pytest.mark.django_db
def test_get_delivery_allocation_unit_without_total(opportunity):
    opportunity.total_budget = 1000
    opportunity.save()
    limited = PaymentUnitFactory(opportunity=opportunity, max_total=10, amount=10, org_amount=0)
    unlimited = PaymentUnitFactory(opportunity=opportunity, max_total=None, max_daily=None)

    allocation = get_delivery_allocation(opportunity)

    by_name = {unit.name: unit for unit in get_payment_unit_breakdown(opportunity).payment_units}
    assert by_name[limited.name].limit == 100
    assert by_name[unlimited.name].limit is None
    assert by_name[unlimited.name].is_reached is False
    assert allocation.total.limit == 100


@pytest.mark.django_db
@pytest.mark.parametrize(
    "unit_org_amount, org_amount, has_org_pay",
    [
        (2, 2, True),
        (0, 0, False),
        # Org pay is still shown after the unit's org amount is removed, as some was already earned
        (0, 2, True),
        # Org pay is shown before any is earned, as the unit pays it
        (2, 0, True),
    ],
)
def test_get_worker_payments(opportunity, unit_org_amount, org_amount, has_org_pay):
    unit = PaymentUnitFactory(opportunity=opportunity, org_amount=unit_org_amount)
    access = OpportunityAccessFactory(opportunity=opportunity, payment_accrued=300)
    PaymentFactory(opportunity_access=access, amount=100)
    CompletedWorkFactory(
        opportunity_access=access,
        payment_unit=unit,
        status=CompletedWorkStatus.approved,
        saved_org_payment_accrued=org_amount * 10,
    )
    CompletedWorkFactory(
        opportunity_access=access,
        payment_unit=unit,
        status=CompletedWorkStatus.pending,
        saved_org_payment_accrued=999,
    )

    payments = get_worker_payments(opportunity)

    assert payments.worker_earned == 300
    assert payments.org_earned == org_amount * 10
    assert payments.paid == 100
    assert payments.has_org_pay is has_org_pay
    assert payments.budget == opportunity.total_budget


@pytest.mark.django_db
@pytest.mark.parametrize("total_budget", [1000, None])
def test_worker_progress_template_renders(opportunity, total_budget):
    opportunity.total_budget = total_budget
    opportunity.save()
    PaymentUnitFactory(opportunity=opportunity, max_total=10, amount=5, org_amount=5)

    html = render_to_string(
        "opportunity/opportunity_worker_progress.html",
        {"allocation": get_delivery_allocation(opportunity), "payments": get_worker_payments(opportunity)},
    )

    assert "Delivery Allocation" in html
    assert "Show Payment Units" in html
    # The payment units are loaded when the breakdown is first opened
    assert "Loading payment units" in html
    budget = "1,000" if total_budget else "--"
    assert f"<strong>{amount_with_currency(0, opportunity.currency_code)}</strong> workers · " in html
    assert f"org / {budget}" in html
    earned_tooltip = "Worker and org pay earned" if total_budget else "No budget is set for this opportunity."
    assert f'x-tooltip.raw="{earned_tooltip}' in html
    if total_budget is None:
        assert "0/--" in html


@pytest.mark.parametrize(
    "completed, warning",
    [(578, False), (900, True), (1000, True), (1050, True)],
)
def test_completed_bar_turns_warning_colour_from_nearing(completed, warning):
    allocation = DeliveryAllocation(
        total=_usage(completed, 1000), has_payment_units=False, active_workers=0, synced_workers=0
    )
    payments = WorkerPayments(currency="", budget=None, worker_earned=0, org_earned=0, paid=0, has_org_pay=False)

    html = render_to_string(
        "opportunity/opportunity_worker_progress.html", {"allocation": allocation, "payments": payments}
    )

    assert ('class="progress-fill bg-brand-marigold"' in html) is warning


@pytest.mark.django_db
def test_get_delivery_allocation_without_payment_units(opportunity):
    allocation = get_delivery_allocation(opportunity)

    assert allocation.has_payment_units is False
    assert get_payment_unit_breakdown(opportunity).payment_units == []


@pytest.mark.django_db
def test_payment_unit_breakdown_template_renders(opportunity):
    unit = PaymentUnitFactory(opportunity=opportunity, max_total=10, max_daily=3, amount=5, org_amount=5)

    html = render_to_string(
        "opportunity/partials/payment_unit_breakdown.html",
        {"breakdown": get_payment_unit_breakdown(opportunity)},
    )

    assert unit.name in html
    assert "Today (all workers):" in html
