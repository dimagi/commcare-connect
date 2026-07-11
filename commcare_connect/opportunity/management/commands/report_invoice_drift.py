import datetime
from collections import Counter

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
