"""Data behind the Delivery Allocation and Payments to Connect Workers cards on the opportunity dashboard."""

from collections import defaultdict
from datetime import datetime, time, timedelta

from django.db.models import Count, Exists, OuterRef, Q, Sum
from django.utils.timezone import localdate, make_aware, now

from commcare_connect.opportunity.helpers import total_accrued_sq, total_org_accrued_sq, total_paid_sq
from commcare_connect.opportunity.models import (
    CompletedWork,
    CompletedWorkStatus,
    DeliverUnit,
    Opportunity,
    OpportunityAccess,
    OpportunityClaimLimit,
    PaymentUnit,
    UserVisit,
    VisitValidationStatus,
)
from commcare_connect.opportunity.worker_progress_types import (
    DailyUsage,
    DeliveryAllocation,
    LimitUsage,
    PaymentUnitBreakdown,
    PaymentUnitUsage,
    StatusCounts,
    WorkerPayments,
)


def get_delivery_allocation(opportunity: Opportunity) -> DeliveryAllocation:
    payment_units = list(opportunity.paymentunit_set.order_by("pk"))
    limit_by_unit = opportunity.allotted_visits_by_unit(payment_units)
    status_counts_by_unit = _status_counts_by_payment_unit(opportunity)
    active_worker_count, synced_worker_count = _active_and_synced_worker_counts(opportunity)

    total_usage = LimitUsage(
        counts=sum((status_counts_by_unit.get(pu.pk, StatusCounts()) for pu in payment_units), StatusCounts()),
        limit=sum(limit_by_unit.values()) if limit_by_unit else None,
    )
    return DeliveryAllocation(
        total=total_usage,
        has_payment_units=bool(payment_units),
        active_workers=active_worker_count,
        synced_workers=synced_worker_count,
    )


def get_payment_unit_breakdown(opportunity: Opportunity) -> PaymentUnitBreakdown:
    """The Delivery Allocation card's per payment unit usage, which the dashboard loads only when it is opened."""
    payment_units = list(opportunity.paymentunit_set.order_by("pk"))
    limit_by_unit = opportunity.allotted_visits_by_unit(payment_units)
    status_counts_by_unit = _status_counts_by_payment_unit(opportunity)
    daily_limit_used_by_unit = _daily_limit_used_by_payment_unit(
        opportunity, [pu for pu in payment_units if pu.max_daily]
    )
    workers_able_to_deliver_by_unit = _workers_able_to_deliver_by_payment_unit(opportunity)

    unit_usages = [
        PaymentUnitUsage(
            counts=status_counts_by_unit.get(pu.pk, StatusCounts()),
            limit=limit_by_unit.get(pu.pk),
            name=pu.name,
            today=DailyUsage(
                used=daily_limit_used_by_unit.get(pu.pk, 0),
                capacity=pu.max_daily * workers_able_to_deliver_by_unit.get(pu.pk, 0),
            )
            if pu.max_daily
            else None,
        )
        for pu in payment_units
    ]
    return PaymentUnitBreakdown(payment_units=unit_usages)


def get_worker_payments(opportunity: Opportunity) -> WorkerPayments:
    # One query; worker pay reuses the Worker Payments card's subqueries so the two cards always agree
    totals = (
        Opportunity.objects.filter(pk=opportunity.pk)
        .select_related("currency")
        .annotate(
            total_accrued=total_accrued_sq(),
            total_paid=total_paid_sq(),
            org_earned=total_org_accrued_sq(),
            has_org_pay_unit=Exists(PaymentUnit.objects.filter(opportunity=OuterRef("pk"), org_amount__gt=0)),
        )
        .get()
    )
    return WorkerPayments(
        currency=totals.currency_code or "",
        budget=totals.total_budget,
        worker_earned=int(totals.total_accrued),
        org_earned=totals.org_earned,
        paid=int(totals.total_paid),
        has_org_pay=totals.org_earned > 0 or totals.has_org_pay_unit,
    )


def _status_counts_by_payment_unit(opportunity):
    # Counted the same way as the Worker Deliver page: approved sums the approved visits, the others count rows
    rows = (
        CompletedWork.objects.filter(opportunity_access__opportunity=opportunity)
        .values("payment_unit_id")
        .annotate(
            approved=Sum("saved_approved_count", filter=Q(status=CompletedWorkStatus.approved)),
            pending=Count("id", filter=Q(status=CompletedWorkStatus.pending)),
            rejected=Count("id", filter=Q(status=CompletedWorkStatus.rejected)),
        )
        .order_by()
    )
    return {
        row["payment_unit_id"]: StatusCounts(
            approved=row["approved"] or 0, pending=row["pending"], rejected=row["rejected"]
        )
        for row in rows
    }


def _daily_limit_used_by_payment_unit(opportunity: Opportunity, payment_units: list[PaymentUnit]) -> dict[int, int]:
    """How much of today's daily limit active workers have used, summed per payment unit.

    The form receiver checks the daily limit against each deliver unit's visits separately. A worker
    can't complete a new delivery today once any required deliver unit has max_daily visits, or once every
    optional deliver unit has, since a delivery needs a visit to each required unit but only one optional
    one. So a worker has used as much of the limit as the higher of its busiest required unit and its
    least busy optional unit. Only active workers count, as only they make up the daily capacity this is
    measured against.
    """
    required_deliver_units, optional_deliver_units = _deliver_units_by_payment_unit(payment_units)
    limit_used_by_payment_unit = defaultdict(int)
    for (_, payment_unit_id), visits_by_deliver_unit in _visits_today_by_worker(opportunity, payment_units).items():
        limit_used_by_payment_unit[payment_unit_id] += _worker_daily_limit_used(
            visits_by_deliver_unit,
            required_deliver_units[payment_unit_id],
            optional_deliver_units[payment_unit_id],
        )
    return limit_used_by_payment_unit


def _visits_today_by_worker(
    opportunity: Opportunity, payment_units: list[PaymentUnit]
) -> dict[tuple[int, int], dict[int, int]]:
    """Active workers' visits today that count toward the daily limit, as
    (worker, payment unit) -> {deliver unit: visits}."""
    start_of_today = make_aware(datetime.combine(localdate(), time.min))
    visit_counts = (
        UserVisit.objects.filter(
            opportunity_access__in=_active_workers(opportunity),
            deliver_unit__payment_unit__in=payment_units,
            visit_date__gte=start_of_today,
        )
        # The visits the form receiver counts toward the daily limit
        .exclude(status__in=[VisitValidationStatus.over_limit, VisitValidationStatus.trial])
        .values("opportunity_access_id", "deliver_unit__payment_unit_id", "deliver_unit_id")
        .annotate(visits=Count("id"))
        .order_by()
    )
    worker_visits = defaultdict(dict)
    for row in visit_counts:
        worker_and_payment_unit = (row["opportunity_access_id"], row["deliver_unit__payment_unit_id"])
        worker_visits[worker_and_payment_unit][row["deliver_unit_id"]] = row["visits"]
    return worker_visits


def _deliver_units_by_payment_unit(payment_units: list[PaymentUnit]):
    """
    The required and the optional deliver units of each payment unit, as two
    dicts with the following format:
        {payment unit: [deliver unit, ...]}
    """
    required_deliver_units, optional_deliver_units = defaultdict(list), defaultdict(list)
    for deliver_unit in DeliverUnit.objects.filter(payment_unit__in=payment_units).values(
        "id", "payment_unit_id", "optional"
    ):
        by_payment_unit = optional_deliver_units if deliver_unit["optional"] else required_deliver_units
        by_payment_unit[deliver_unit["payment_unit_id"]].append(deliver_unit["id"])
    return required_deliver_units, optional_deliver_units


def _worker_daily_limit_used(
    visits_by_deliver_unit: dict[int, int],
    required_deliver_units: list[int],
    optional_deliver_units: list[int],
) -> int:
    used = max((visits_by_deliver_unit.get(du, 0) for du in required_deliver_units), default=0)
    if optional_deliver_units:
        used = max(used, min(visits_by_deliver_unit.get(du, 0) for du in optional_deliver_units))
    return used


def _active_workers(opportunity):
    """Workers who have claimed the opportunity, are not suspended and whose claim has not ended."""
    return OpportunityAccess.objects.filter(
        opportunity=opportunity, suspended=False, opportunityclaim__end_date__gte=localdate()
    )


def _workers_able_to_deliver_by_payment_unit(opportunity):
    """
    Active workers holding a current claim limit for each payment unit.
    """
    rows = (
        OpportunityClaimLimit.objects.filter(opportunity_claim__opportunity_access__in=_active_workers(opportunity))
        .filter(Q(end_date__isnull=True) | Q(end_date__gte=localdate()))
        .values("payment_unit_id")
        .annotate(workers=Count("opportunity_claim__opportunity_access", distinct=True))
        .order_by()
    )
    return {row["payment_unit_id"]: row["workers"] for row in rows}


def _active_and_synced_worker_counts(opportunity):
    """Active workers, and those of them with a delivery received by the server in the last 24 hours."""
    received = UserVisit.objects.filter(
        opportunity_access=OuterRef("pk"), date_created__gte=now() - timedelta(hours=24)
    )
    counts = _active_workers(opportunity).aggregate(active=Count("id"), synced=Count("id", filter=Q(Exists(received))))
    return counts["active"], counts["synced"]
