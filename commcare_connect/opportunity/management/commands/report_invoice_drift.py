import argparse
import csv
import datetime
import os
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from django.contrib.sites.models import Site
from django.core.management import BaseCommand
from django.db.models import Count, Q
from django.urls import reverse

from commcare_connect.opportunity.models import (
    CompletedWork,
    DeliverUnit,
    InvoiceStatus,
    PaymentInvoice,
    PaymentUnit,
    UserVisit,
    VisitReviewStatus,
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
    """Deliver-unit ids for visits approved by STATUS ONLY on/before as_of_date.

    Visits with a NULL status_modified_date ARE counted: a NULL means the approval date was never
    recorded (a data-quality gap), so we can't place the visit in time and assume it was billed (excluding
    them manufactured phantom late-delivery drift). If that assumption is wrong the reconstruction
    over-counts and reconstruction_gap goes non-zero, flagging the row — legacy_null_status_date_visits
    shows how many such visits are involved.
    """
    return completed_work.uservisit_set.filter(
        Q(status_modified_date__date__lte=as_of_date) | Q(status_modified_date__isnull=True),
        status=VisitValidationStatus.approved,
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


def _late_approved_visit_count(completed_work, as_of_date):
    """Raw count of visits approved AFTER the invoice generation date (status=approved, status_modified_date > it).

    These are the deliveries that landed too late to be billed on this invoice and therefore explain
    late-delivery drift. Sums this completed work's late visits plus those of its child completed works
    recursively.

    NOTE: this is a raw visit tally, NOT a delivery count, so it does NOT equal late_delivery_units in
    general.
    """
    count = completed_work.uservisit_set.filter(
        status=VisitValidationStatus.approved,
        status_modified_date__date__gt=as_of_date,
    ).count()
    count += sum(_late_approved_visit_count(child, as_of_date) for child in _child_completed_works(completed_work))
    return count


def _null_status_date_approved_visit_count(completed_work):
    count = completed_work.uservisit_set.filter(
        status=VisitValidationStatus.approved,
        status_modified_date__isnull=True,
    ).count()
    count += sum(_null_status_date_approved_visit_count(child) for child in _child_completed_works(completed_work))
    return count


def invoice_review_url(invoice, base_url=""):
    opportunity = invoice.opportunity
    path = reverse(
        "opportunity:invoice_review",
        args=(opportunity.organization.slug, opportunity.opportunity_id, invoice.payment_invoice_id),
    )
    return f"{base_url.rstrip('/')}{path}" if base_url else path


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
    (no review_status=agree gate) and (2) bounds them to status_modified_date <= as_of_date, treating a
    NULL status_modified_date as billed (see _approved_deliver_unit_ids). Child payment units are
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
    # deliveries approved after the invoice end date; the raw evidence behind late-delivery drift.
    late_approved_visit_count: int
    # Late-delivery drift, valued at the FULL per-unit rate (flw_amount + org_amount). Org pay is INCLUDED here.
    late_delivery_drift: Decimal
    # overbilled_units: max(0, reconstructed - desired): billed under the legacy rule but never
    # PM-agreed (or otherwise not owed now) — a clawback candidate.
    overbilled_units: int
    # Over-billing drift, also valued at the FULL rate (flw_amount + org_amount); org pay included.
    overbilled_drift: Decimal
    # reconstructed_deliveries_count * org_amount: org omitted on billed units (reporting only).
    org_pay_drift: Decimal
    # Approved visits counted with an unrecorded status date (see _null_status_date_approved_visit_count).
    legacy_null_status_date_visits: int


@dataclass
class InvoiceDrift:
    invoice_id: int
    invoice_number: str
    invoice_url: str
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
    # Per-line exposure roll-up, NOT a distinct-visit count: a composite's child visit is counted on both
    # the child and parent rows (intentional — a child visit explains the parent's drift), so it sums twice.
    legacy_null_status_date_visits: int
    works: list = field(default_factory=list)


@dataclass
class _InvoiceLookups:
    recon: dict  # completed_work_id -> Counter(deliver_unit_id -> n): approved by status, date-bounded or NULL
    agreed: dict  # completed_work_id -> Counter(deliver_unit_id -> n): approved AND agreed (current owed basis)
    late: dict  # completed_work_id -> n: approved with status_modified_date after as_of_date
    null: dict  # completed_work_id -> n: approved with NULL status_modified_date
    du_meta: dict  # payment_unit_id -> (required_deliver_unit_ids, optional_deliver_unit_ids)
    parents_with_children: set  # payment_unit_ids that have child payment units


def _build_invoice_lookups(invoice, as_of_date):
    """Batch every per-completed-work count this invoice's works need into a handful of grouped queries.

    Replaces the per-work N+1 (each work previously issued ~6-8 queries, so a 10k-work invoice ran ~75k
    queries) with a constant number of queries per invoice. Scoped to the invoice's own works; child
    (composite) works are rare and still resolved via the query path inside the count functions.
    """
    approved = VisitValidationStatus.approved
    invoice_visits = UserVisit.objects.filter(completed_work__invoice=invoice, status=approved)

    recon = defaultdict(Counter)
    for row in (
        invoice_visits.filter(Q(status_modified_date__date__lte=as_of_date) | Q(status_modified_date__isnull=True))
        .values("completed_work_id", "deliver_unit_id")
        .annotate(n=Count("id"))
    ):
        recon[row["completed_work_id"]][row["deliver_unit_id"]] = row["n"]

    agreed = defaultdict(Counter)
    for row in (
        invoice_visits.filter(review_status=VisitReviewStatus.agree)
        .values("completed_work_id", "deliver_unit_id")
        .annotate(n=Count("id"))
    ):
        agreed[row["completed_work_id"]][row["deliver_unit_id"]] = row["n"]

    late = {
        row["completed_work_id"]: row["n"]
        for row in invoice_visits.filter(status_modified_date__date__gt=as_of_date)
        .values("completed_work_id")
        .annotate(n=Count("id"))
    }
    null = {
        row["completed_work_id"]: row["n"]
        for row in invoice_visits.filter(status_modified_date__isnull=True)
        .values("completed_work_id")
        .annotate(n=Count("id"))
    }

    pu_ids = list(CompletedWork.objects.filter(invoice=invoice).values_list("payment_unit_id", flat=True).distinct())
    du_meta = {pu_id: ([], []) for pu_id in pu_ids}
    for row in DeliverUnit.objects.filter(payment_unit_id__in=pu_ids).values("payment_unit_id", "id", "optional"):
        required, optional = du_meta[row["payment_unit_id"]]
        (optional if row["optional"] else required).append(row["id"])

    parents_with_children = set(
        PaymentUnit.objects.filter(parent_payment_unit_id__in=pu_ids).values_list("parent_payment_unit_id", flat=True)
    )

    return _InvoiceLookups(recon, agreed, late, null, du_meta, parents_with_children)


def _delivery_count(unit_counts, required, optional):
    """min-across-required delivery count, capped by the optional total; from a deliver_unit_id -> n map.

    Same rule as reconstruct_billed_count / calculate_completed for a work with no children.
    """
    number = min((unit_counts[du_id] for du_id in required), default=0)
    if optional:
        number = min(number, sum(unit_counts[du_id] for du_id in optional))
    return number


def compute_invoice_drift(invoice, base_url=""):
    works = []
    reconstructed_flw_amount = Decimal(0)
    completed_works = CompletedWork.objects.filter(invoice=invoice).select_related("payment_unit")
    # invoice.date is date-only, so the whole generation day is counted; in the rare case a visit was approved
    # later that same day (after the invoice was cut) it is slightly over-counted, which reconstruction_gap
    # surfaces if ever material.
    billed_through = invoice.date
    lookups = _build_invoice_lookups(invoice, billed_through)
    for cw in completed_works:
        is_composite = cw.payment_unit_id in lookups.parents_with_children
        if is_composite:
            reconstructed_deliveries_count = reconstruct_billed_count(cw, billed_through)
            desired_deliveries_count = cw.approved_count
        else:
            required, optional = lookups.du_meta[cw.payment_unit_id]
            reconstructed_deliveries_count = _delivery_count(lookups.recon.get(cw.id, Counter()), required, optional)
            desired_deliveries_count = _delivery_count(lookups.agreed.get(cw.id, Counter()), required, optional)

        flw_amount = cw.payment_unit.amount
        org_amount = cw.payment_unit.org_amount
        full_rate = flw_amount + org_amount
        late_units = max(0, desired_deliveries_count - reconstructed_deliveries_count)
        over_units = max(0, reconstructed_deliveries_count - desired_deliveries_count)

        if is_composite:
            late_approved_visits = _late_approved_visit_count(cw, billed_through) if late_units else 0
            null_date_visits = _null_status_date_approved_visit_count(cw)
        else:
            late_approved_visits = lookups.late.get(cw.id, 0) if late_units else 0
            null_date_visits = lookups.null.get(cw.id, 0)
        works.append(
            WorkDrift(
                completed_work_id=cw.id,
                payment_unit_name=cw.payment_unit.name,
                entity_name=cw.entity_name or "",
                reconstructed_deliveries_count=reconstructed_deliveries_count,
                desired_deliveries_count=desired_deliveries_count,
                late_delivery_units=late_units,
                late_approved_visit_count=late_approved_visits,
                late_delivery_drift=Decimal(late_units * full_rate),
                overbilled_units=over_units,
                overbilled_drift=Decimal(over_units * full_rate),
                org_pay_drift=Decimal(reconstructed_deliveries_count * org_amount),
                legacy_null_status_date_visits=null_date_visits,
            )
        )
        reconstructed_flw_amount += reconstructed_deliveries_count * flw_amount

    frozen = invoice.amount
    return InvoiceDrift(
        invoice_id=invoice.id,
        invoice_number=invoice.invoice_number,
        invoice_url=invoice_review_url(invoice, base_url),
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
        legacy_null_status_date_visits=sum(w.legacy_null_status_date_visits for w in works),
        works=works,
    )


INVOICE_CSV_HEADER = [
    "opportunity_id",
    "opportunity_name",
    "org_name",
    "invoice_id",
    "invoice_number",
    "invoice_url",
    "invoice_status",
    "invoice_date",
    "frozen_amount",
    "reconstructed_flw_amount",
    "reconstruction_gap",
    "late_delivery_units",
    "late_delivery_drift",
    "overbilled_units",
    "overbilled_drift",
    "org_pay_drift",
    "legacy_null_status_date_visits",
]

WORK_CSV_HEADER = [
    "invoice_id",
    "invoice_number",
    "opportunity_id",
    "completed_work_id",
    "payment_unit",
    "entity_name",
    "reconstructed_deliveries_count",
    "desired_deliveries_count",
    "late_delivery_units",
    "late_approved_visit_count",
    "late_delivery_drift",
    "overbilled_units",
    "overbilled_drift",
    "org_pay_drift",
    "legacy_null_status_date_visits",
]


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


def iter_invoice_drift(invoices, base_url=""):
    invoices = list(invoices)
    total = len(invoices)
    print(f"Scanning {total} invoices...", flush=True)
    for i, invoice in enumerate(invoices, 1):
        print(f"[{i}/{total}] {invoice.id}  computing drift...", flush=True)
        drift = compute_invoice_drift(invoice, base_url)
        # Per-invoice progress: the run is a per-completed-work N+1, so a slow line = an invoice with many works.
        print(f"[{i}/{total}] {invoice.id}  works={len(drift.works)}  gap={drift.reconstruction_gap}", flush=True)
        yield drift


def invoice_row(drift):
    return [
        drift.opportunity_id,
        drift.opportunity_name,
        drift.org_name,
        drift.invoice_id,
        drift.invoice_number,
        drift.invoice_url,
        drift.status,
        drift.invoice_date,
        drift.frozen_amount,
        drift.reconstructed_flw_amount,
        drift.reconstruction_gap,
        drift.late_delivery_units,
        drift.late_delivery_drift,
        drift.overbilled_units,
        drift.overbilled_drift,
        drift.org_pay_drift,
        drift.legacy_null_status_date_visits,
    ]


def work_has_drift(work):
    """A work is worth a row only if it under- or over-delivered (positive or negative count drift)."""
    return work.late_delivery_units > 0 or work.overbilled_units > 0


def work_rows(drift):
    for work in drift.works:
        if not work_has_drift(work):
            continue
        yield [
            drift.invoice_id,
            drift.invoice_number,
            drift.opportunity_id,
            work.completed_work_id,
            work.payment_unit_name,
            work.entity_name,
            work.reconstructed_deliveries_count,
            work.desired_deliveries_count,
            work.late_delivery_units,
            work.late_approved_visit_count,
            work.late_delivery_drift,
            work.overbilled_units,
            work.overbilled_drift,
            work.org_pay_drift,
            work.legacy_null_status_date_visits,
        ]


def write_invoice_csv(drifts, path):
    """One row per drifting invoice."""
    with open(path, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(INVOICE_CSV_HEADER)
        for drift in drifts:
            if invoice_has_drift(drift):
                writer.writerow(invoice_row(drift))


def write_work_csv(drifts, path):
    """One row per completed work, for drifting invoices only; join to the invoice report on invoice_id."""
    with open(path, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(WORK_CSV_HEADER)
        for drift in drifts:
            if not invoice_has_drift(drift):
                continue
            for row in work_rows(drift):
                writer.writerow(row)


def summary_lines(drifts):
    reportable = [d for d in drifts if invoice_has_drift(d)]
    late = sum(1 for d in drifts if d.late_delivery_units > 0)
    late_units = sum(d.late_delivery_units for d in drifts)
    over = sum(1 for d in drifts if d.overbilled_units > 0)
    over_units = sum(d.overbilled_units for d in drifts)
    unexplained_gap = sum(1 for d in drifts if gap_is_unexplained(d))
    return [
        f"Invoices scanned: {len(drifts)}",
        f"Invoices shown (correctable / anomalous drift): {len(reportable)}",
        f"Late-delivery drift (incl org): {late} invoice(s), {late_units} unit(s)",
        f"Over-billing drift (clawback, incl org): {over} invoice(s), {over_units} unit(s)",
        f"Reconstruction gap unexplained (model-fit anomalies): {unexplained_gap} invoice(s)",
    ]


class Command(BaseCommand):
    help = "Read-only legacy invoice drift report (CCCT-2524). Writes a CSV and prints a summary."

    def add_arguments(self, parser):
        parser.add_argument("--opportunity", type=int, default=None)
        parser.add_argument("--program", type=int, default=None)
        parser.add_argument(
            "--output", type=str, default="/tmp/invoice_drift_report.csv", help="Invoice-level report path."
        )
        parser.add_argument(
            "--exclude-test",
            action=argparse.BooleanOptionalAction,
            default=True,
            help="Exclude test opportunities (is_test=True). Defaults to True; --no-exclude-test to include them.",
        )

    def handle(self, *args, **options):
        print("Generating invoice drift report...")
        invoices = service_delivery_invoices(
            opportunity_id=options["opportunity"],
            program_id=options["program"],
            exclude_test=options["exclude_test"],
        )
        base_url = f"https://{Site.objects.get_current().domain}"
        drifts = list(iter_invoice_drift(invoices, base_url=base_url))

        for drift in drifts:
            if gap_is_unexplained(drift):
                self.stderr.write(
                    self.style.WARNING(
                        f"Reconstruction gap for invoice {drift.invoice_number}: frozen "
                        f"{drift.frozen_amount} vs reconstructed_flw_amount {drift.reconstructed_flw_amount} "
                        f"(gap {drift.reconstruction_gap})"
                    )
                )
        for line in summary_lines(drifts):
            self.stdout.write(line)

        if drifts:
            invoice_output = options["output"]
            base, ext = os.path.splitext(invoice_output)
            work_output = f"{base}_works{ext}"
            write_invoice_csv(drifts, invoice_output)
            write_work_csv(drifts, work_output)
            self.stdout.write(self.style.SUCCESS(f"Invoice report written to {os.path.abspath(invoice_output)}"))
            self.stdout.write(self.style.SUCCESS(f"Work report written to {os.path.abspath(work_output)}"))
