import datetime
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal

from commcare_connect.opportunity.models import (
    CompletedWork,
    InvoiceStatus,
    PaymentInvoice,
    VisitValidationStatus,
)

EXCLUDED_STATUSES = [InvoiceStatus.CANCELLED_BY_NM, InvoiceStatus.REJECTED_BY_PM]

# Automated service-delivery invoicing only went live on 2026-01-01. Invoices generated before then were
# not produced by that pipeline, so the pre-agreement reconstruction does not apply — exclude them.
AUTOMATED_INVOICE_START_DATE = datetime.date(2026, 1, 1)


def service_delivery_invoices(opportunity_id=None, program_id=None, exclude_test=True):
    qs = (
        PaymentInvoice.objects.filter(
            service_delivery=True,
            end_date__isnull=False,
            date__gte=AUTOMATED_INVOICE_START_DATE,
        )
        .exclude(status__in=EXCLUDED_STATUSES)
        .filter(completedwork__isnull=False)
        .select_related("opportunity__organization")
        .distinct()
    )
    if exclude_test:
        qs = qs.filter(opportunity__is_test=False)
    if opportunity_id:
        qs = qs.filter(opportunity_id=opportunity_id)
    if program_id:
        qs = qs.filter(opportunity__program_id=program_id)
    return qs.order_by("opportunity_id", "id")


def _approved_deliver_unit_ids(completed_work, as_of_date):
    """Deliver-unit ids for visits approved by STATUS ONLY on/before as_of_date."""
    return completed_work.uservisit_set.filter(
        status=VisitValidationStatus.approved,
        status_modified_date__date__lte=as_of_date,
    ).values_list("deliver_unit_id", flat=True)


def _child_completed_works(completed_work):
    """Completed works for this work's child payment units (same access + entity), or an empty queryset."""
    child_payment_units = completed_work.payment_unit.child_payment_units.all()
    if not child_payment_units:
        return CompletedWork.objects.none()
    return CompletedWork.objects.filter(
        opportunity_access=completed_work.opportunity_access,
        payment_unit__in=child_payment_units,
        entity_id=completed_work.entity_id,
    )


def reconstruct_billed_count(completed_work, as_of_date):
    """Reconstruct the approved count billed at invoice time, using the *pre-agreement* rules.

    Pre-agreement (before CCCT-2505): a work needed >=1 PM-agreed visit to be billable, but the billed
    COUNT then came from approval STATUS (status=approved), not the agreed visits. CCCT-2505 restricted
    the count to agreed visits only. Counting by status alone is safe here because we only reconstruct
    already-invoiced works, where that "at least one agreed" gate was already met.

    ``as_of_date`` is the invoice GENERATION date (invoice.date), not its service-period end_date: an
    invoice freezes its count at generation, so visits approved between end_date and then were still
    billed. Bounding at end_date instead would drop them and manufacture phantom late-delivery drift.

    Mirrors CompletedWork.calculate_completed(approved=True) but (1) counts visits by approval STATUS only
    (no review_status=agree gate) and (2) bounds them to status_modified_date <= as_of_date. Child payment units are
    reconstructed recursively with the same rules rather than via the live, agreement-gated approved_count.
    """
    unit_counts = Counter(_approved_deliver_unit_ids(completed_work, as_of_date))
    deliver_units = completed_work.payment_unit.deliver_units.values("id", "optional")

    required = []
    optional = []
    for du in deliver_units:
        if du["optional"]:
            optional.append(du["id"])
        else:
            required.append(du["id"])

    number_approved = min((unit_counts[du_id] for du_id in required), default=0)
    if optional:
        number_approved = min(number_approved, sum(unit_counts[du_id] for du_id in optional))

    children = _child_completed_works(completed_work)
    if children:
        child_count = sum(reconstruct_billed_count(child, as_of_date) for child in children)
        number_approved = min(number_approved, child_count)
    return number_approved


@dataclass
class WorkDrift:
    completed_work_id: int
    payment_unit_name: str
    entity_name: str
    reconstructed_deliveries_count: int  # old logic, in-invoice-window (what was billed)
    desired_deliveries_count: int  # current agreement-gated logic, all-time (what is owed now)
    late_delivery_units: int  # max(0, desired - reconstructed)
    # Late-delivery drift, valued at the FULL per-unit rate (flw_amount + org_amount). Org pay is INCLUDED here.
    late_delivery_drift: Decimal
    # overbilled_units: max(0, reconstructed - desired): billed under the legacy rule but never
    # PM-agreed (or otherwise not owed now) — a clawback candidate.
    overbilled_units: int
    # Over-billing drift, also valued at the FULL rate (flw_amount + org_amount); org pay included.
    overbilled_drift: Decimal
    # reconstructed_deliveries_count * org_amount: org omitted on billed units (reporting only).
    org_pay_drift: Decimal


@dataclass
class InvoiceDrift:
    invoice_id: int
    invoice_number: str
    opportunity_id: int
    opportunity_name: str
    org_name: str
    status: str
    invoice_date: object
    frozen_amount: Decimal
    reconstructed_flw_amount: Decimal  # Σ reconstructed_deliveries_count * flw_amount;
    late_delivery_units: int
    late_delivery_drift: Decimal
    overbilled_units: int
    overbilled_drift: Decimal
    # org pay omitted on the units that WERE billed (before CCCT-2470 fix)
    org_pay_drift: Decimal
    # reconstruction_gap = frozen_amount - reconstructed_flw_amount.
    # This compares the ACTUAL frozen invoice vs our reconstruction of old-logic
    # billing i.e. "does our reconstruction even reproduce what was billed?" It is the confidence gauge for
    # every other number on the row. Interpret:
    #   ≈ 0              → reconstruction reproduces the invoice (only FLW amounts); trust the drift.
    #   ≈ org_pay_drift  → the invoice already billed org pay, so org_pay_drift is NOT actually missing
    #                      (a false positive for this row).
    #   anything else    → reconstruction can't reproduce the frozen amount; drift numbers are unreliable —
    #                      review this.
    reconstruction_gap: Decimal
    works: list = field(default_factory=list)


def compute_invoice_drift(invoice):
    works = []
    reconstructed_flw_amount = Decimal(0)
    completed_works = CompletedWork.objects.filter(invoice=invoice).select_related("payment_unit")
    # invoice.date is date-only, so the whole generation day is counted; in the rare case a visit was approved
    # later that same day (after the invoice was cut) it is slightly over-counted, which reconstruction_gap
    # surfaces if ever material.
    billed_through = invoice.date
    for cw in completed_works:
        reconstructed_deliveries_count = reconstruct_billed_count(cw, billed_through)
        desired_deliveries_count = cw.approved_count
        flw_amount = cw.payment_unit.amount
        org_amount = cw.payment_unit.org_amount
        full_rate = flw_amount + org_amount
        late_units = max(0, desired_deliveries_count - reconstructed_deliveries_count)
        over_units = max(0, reconstructed_deliveries_count - desired_deliveries_count)
        works.append(
            WorkDrift(
                completed_work_id=cw.id,
                payment_unit_name=cw.payment_unit.name,
                entity_name=cw.entity_name or "",
                reconstructed_deliveries_count=reconstructed_deliveries_count,
                desired_deliveries_count=desired_deliveries_count,
                late_delivery_units=late_units,
                late_delivery_drift=Decimal(late_units * full_rate),
                overbilled_units=over_units,
                overbilled_drift=Decimal(over_units * full_rate),
                org_pay_drift=Decimal(reconstructed_deliveries_count * org_amount),
            )
        )
        reconstructed_flw_amount += reconstructed_deliveries_count * flw_amount

    frozen = invoice.amount
    return InvoiceDrift(
        invoice_id=invoice.id,
        invoice_number=invoice.invoice_number,
        opportunity_id=invoice.opportunity_id,
        opportunity_name=invoice.opportunity.name,
        org_name=invoice.opportunity.organization.name,
        status=invoice.status,
        invoice_date=invoice.date,
        frozen_amount=frozen,
        reconstructed_flw_amount=reconstructed_flw_amount,
        late_delivery_units=sum(w.late_delivery_units for w in works),
        late_delivery_drift=sum((w.late_delivery_drift for w in works), Decimal(0)),
        overbilled_units=sum(w.overbilled_units for w in works),
        overbilled_drift=sum((w.overbilled_drift for w in works), Decimal(0)),
        org_pay_drift=sum((w.org_pay_drift for w in works), Decimal(0)),
        reconstruction_gap=frozen - reconstructed_flw_amount,
        works=works,
    )


def gap_is_unexplained(drift):
    """True when the reconstruction gap is NOT explained by the invoice's era.
    gap == 0            → FLW-era invoice(only flw amounts), reconstruction reproduces it exactly.
    gap == org_pay_drift → recent org-era invoice(flw + org amounts) that already billed org pay (org not missing).
    Anything else is a genuine model-fit anomaly.
    """
    return drift.reconstruction_gap not in (Decimal(0), drift.org_pay_drift)


def invoice_has_drift(drift):
    """True if the invoice shows correctable or anomalous drift, i.e. worth showing."""
    return drift.late_delivery_units > 0 or drift.overbilled_units > 0 or gap_is_unexplained(drift)


def iter_invoice_drift(invoices):
    for invoice in invoices:
        yield compute_invoice_drift(invoice)
