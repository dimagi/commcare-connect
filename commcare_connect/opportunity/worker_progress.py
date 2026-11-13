"""Data behind the Delivery Allocation and Payments to Connect Workers cards on the opportunity dashboard."""

from datetime import timedelta

from django.db.models import Count, Exists, OuterRef, Q, Sum
from django.utils.timezone import localdate, now

from commcare_connect.opportunity.helpers import total_accrued_sq, total_org_accrued_sq, total_paid_sq
from commcare_connect.opportunity.models import (
    CompletedWork,
    CompletedWorkStatus,
    Opportunity,
    OpportunityAccess,
    PaymentUnit,
    UserVisit,
)
from commcare_connect.opportunity.worker_progress_types import (
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

    unit_usages = [
        PaymentUnitUsage(
            counts=status_counts_by_unit.get(pu.pk, StatusCounts()),
            limit=limit_by_unit.get(pu.pk),
            name=pu.name,
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


def _active_workers(opportunity):
    """Workers who have claimed the opportunity, are not suspended and whose claim has not ended."""
    return OpportunityAccess.objects.filter(
        opportunity=opportunity, suspended=False, opportunityclaim__end_date__gte=localdate()
    )


def _active_and_synced_worker_counts(opportunity):
    """Active workers, and those of them with a delivery received by the server in the last 24 hours."""
    received = UserVisit.objects.filter(
        opportunity_access=OuterRef("pk"), date_created__gte=now() - timedelta(hours=24)
    )
    counts = _active_workers(opportunity).aggregate(active=Count("id"), synced=Count("id", filter=Q(Exists(received))))
    return counts["active"], counts["synced"]
