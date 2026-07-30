import datetime
from decimal import Decimal

import pytest

from commcare_connect.opportunity.management.commands.report_invoice_drift import (
    _late_approved_visit_count,
    _null_status_date_approved_visit_count,
    compute_invoice_drift,
    gap_is_unexplained,
    invoice_has_drift,
    iter_invoice_drift,
    reconstruct_billed_count,
    service_delivery_invoices,
    summary_lines,
    work_rows,
    write_invoice_csv,
    write_work_csv,
)
from commcare_connect.opportunity.models import InvoiceStatus, UserVisit, VisitReviewStatus, VisitValidationStatus
from commcare_connect.opportunity.tests.factories import (
    CompletedWorkFactory,
    DeliverUnitFactory,
    OpportunityAccessFactory,
    OpportunityFactory,
    PaymentInvoiceFactory,
    PaymentUnitFactory,
    UserVisitFactory,
)

pytestmark = pytest.mark.django_db

END = datetime.date(2026, 1, 31)


def _invoice_with_work(opportunity, *, amount, status=InvoiceStatus.PAID, service_delivery=True):
    invoice = PaymentInvoiceFactory(
        opportunity=opportunity,
        amount=Decimal(amount),
        service_delivery=service_delivery,
        status=status,
        end_date=END,
        date=END,
    )
    access = OpportunityAccessFactory(opportunity=opportunity)
    pu = PaymentUnitFactory(opportunity=opportunity, amount=100, org_amount=0)
    cw = CompletedWorkFactory(opportunity_access=access, payment_unit=pu, saved_approved_count=1)
    cw.invoice = invoice
    cw.save()
    return invoice, cw, pu


def test_query_excludes_cancelled_rejected_and_non_service_delivery(opportunity):
    live, _, _ = _invoice_with_work(opportunity, amount=100, status=InvoiceStatus.PAID)
    _invoice_with_work(opportunity, amount=100, status=InvoiceStatus.CANCELLED_BY_NM)
    _invoice_with_work(opportunity, amount=100, status=InvoiceStatus.REJECTED_BY_PM)
    _invoice_with_work(opportunity, amount=100, service_delivery=False)

    result = list(service_delivery_invoices())
    assert [i.id for i in result] == [live.id]


def test_query_excludes_test_opportunities_by_default(opportunity):
    real = OpportunityFactory(is_test=False)
    test_opp = OpportunityFactory(is_test=True)
    real_invoice, _, _ = _invoice_with_work(real, amount=100, status=InvoiceStatus.PAID)
    test_invoice, _, _ = _invoice_with_work(test_opp, amount=100, status=InvoiceStatus.PAID)

    assert [i.id for i in service_delivery_invoices()] == [real_invoice.id]
    assert test_invoice.id in {i.id for i in service_delivery_invoices(exclude_test=False)}


def _approved_visit(cw, deliver_unit, *, on, agree=False):
    """Create an approved visit and force status/status_modified_date/review_status (bypasses __setattr__).

    agree=True → review_status=agree (counts under the current agreement-gated logic / approved_count);
    agree=False → review_status=pending (counts only under the pre-agreement reconstruction).
    """
    visit = UserVisitFactory(
        opportunity=cw.opportunity_access.opportunity,
        opportunity_access=cw.opportunity_access,
        completed_work=cw,
        deliver_unit=deliver_unit,
    )
    UserVisit.objects.filter(pk=visit.pk).update(
        status=VisitValidationStatus.approved,
        status_modified_date=on,
        review_status=VisitReviewStatus.agree if agree else VisitReviewStatus.pending,
    )
    return visit


def test_reconstruct_counts_only_visits_approved_by_end_date(opportunity):
    access = OpportunityAccessFactory(opportunity=opportunity)
    pu = PaymentUnitFactory(opportunity=opportunity, amount=100, org_amount=0)
    du = DeliverUnitFactory(payment_unit=pu, optional=False)
    cw = CompletedWorkFactory(opportunity_access=access, payment_unit=pu)

    _approved_visit(cw, du, on=datetime.datetime(2026, 1, 15, tzinfo=datetime.UTC))
    _approved_visit(cw, du, on=datetime.datetime(2026, 2, 15, tzinfo=datetime.UTC))

    # Only the January visit is within the invoice window.
    assert reconstruct_billed_count(cw, END) == 1


def test_null_status_date_visits_are_counted_as_billed_and_surfaced(opportunity):
    """Some visits have a NULL status_modified_date because the approval timestamp was never recorded (a
    known data-quality gap, not tied to age). We assume such visits were billed and COUNT them in the
    reconstruction (excluding them manufactured phantom late drift); their presence is also surfaced via
    _null_status_date_approved_visit_count so a bad assumption shows up against reconstruction_gap."""
    access = OpportunityAccessFactory(opportunity=opportunity)
    pu = PaymentUnitFactory(opportunity=opportunity, amount=100, org_amount=0)
    du = DeliverUnitFactory(payment_unit=pu, optional=False)
    cw = CompletedWorkFactory(opportunity_access=access, payment_unit=pu)

    _approved_visit(cw, du, on=None)

    assert reconstruct_billed_count(cw, END) == 1  # unknown date → assumed billed, counted
    assert _late_approved_visit_count(cw, END) == 0  # a NULL-date visit is never "late" either
    assert _null_status_date_approved_visit_count(cw) == 1  # and its presence is surfaced


def test_reconstruct_excludes_rejected_visits(opportunity):
    access = OpportunityAccessFactory(opportunity=opportunity)
    pu = PaymentUnitFactory(opportunity=opportunity, amount=100, org_amount=0)
    du = DeliverUnitFactory(payment_unit=pu, optional=False)
    cw = CompletedWorkFactory(opportunity_access=access, payment_unit=pu)

    visit = _approved_visit(cw, du, on=datetime.datetime(2026, 1, 15, tzinfo=datetime.UTC))
    # Post-invoice rejection overwrites status + status_modified_date.
    UserVisit.objects.filter(pk=visit.pk).update(
        status=VisitValidationStatus.rejected,
        status_modified_date=datetime.datetime(2026, 2, 15, tzinfo=datetime.UTC),
    )

    assert reconstruct_billed_count(cw, END) == 0


def test_reconstruct_child_constraint_uses_pre_agreement_basis(opportunity):
    """A child work approved-but-not-agreed still counts (legacy basis), unlike live approved_count."""
    access = OpportunityAccessFactory(opportunity=opportunity)
    parent_pu = PaymentUnitFactory(opportunity=opportunity, amount=100, org_amount=0)
    child_pu = PaymentUnitFactory(opportunity=opportunity, amount=0, org_amount=0)
    child_pu.parent_payment_unit = parent_pu
    child_pu.save()
    parent_du = DeliverUnitFactory(payment_unit=parent_pu, optional=False)
    child_du = DeliverUnitFactory(payment_unit=child_pu, optional=False)

    parent_cw = CompletedWorkFactory(opportunity_access=access, payment_unit=parent_pu, entity_id="e1")
    child_cw = CompletedWorkFactory(opportunity_access=access, payment_unit=child_pu, entity_id="e1")

    _approved_visit(parent_cw, parent_du, on=datetime.datetime(2026, 1, 15, tzinfo=datetime.UTC))
    child_visit = _approved_visit(child_cw, child_du, on=datetime.datetime(2026, 1, 15, tzinfo=datetime.UTC))
    # Child visit is approved but was never PM-agreed → excluded by live approved_count, kept by reconstruction.
    UserVisit.objects.filter(pk=child_visit.pk).update(review_status=VisitReviewStatus.pending)

    assert parent_cw.approved_count == 0  # live, agreement-gated logic drops it
    assert reconstruct_billed_count(parent_cw, END) == 1  # legacy basis keeps it, parent constrained by child


IN_WINDOW = datetime.datetime(2026, 1, 15, tzinfo=datetime.UTC)
AFTER_WINDOW = datetime.datetime(2026, 2, 15, tzinfo=datetime.UTC)
# An invoice whose service period ends END (Jan 31) but that was generated later (Feb 28): visits
# approved between END and the generation date were still billed.
GENERATED_ON = datetime.date(2026, 2, 28)


def _completed_work_with_visits(
    invoice,
    opportunity,
    *,
    amount,
    org_amount,
    agreed_in_window=0,
    approved_only_in_window=0,
    agreed_late=0,
):
    """Build a completed work linked to `invoice` from real visits, so both counts are recalculated:

    reconstructed (old logic, status-only, <= end_date) = agreed_in_window + approved_only_in_window
    desired (cw.approved_count, agreement-gated, all-time) = agreed_in_window + agreed_late
    """
    access = OpportunityAccessFactory(opportunity=opportunity)
    pu = PaymentUnitFactory(opportunity=opportunity, amount=amount, org_amount=org_amount)
    du = DeliverUnitFactory(payment_unit=pu, optional=False)
    cw = CompletedWorkFactory(opportunity_access=access, payment_unit=pu)
    cw.invoice = invoice
    cw.save()
    for _ in range(agreed_in_window):
        _approved_visit(cw, du, on=IN_WINDOW, agree=True)
    for _ in range(approved_only_in_window):
        _approved_visit(cw, du, on=IN_WINDOW, agree=False)
    for _ in range(agreed_late):
        _approved_visit(cw, du, on=AFTER_WINDOW, agree=True)
    return cw


def test_no_drift(opportunity):
    invoice = PaymentInvoiceFactory(
        opportunity=opportunity,
        amount=Decimal(100),
        service_delivery=True,
        status=InvoiceStatus.PAID,
        end_date=END,
        date=END,
    )
    _completed_work_with_visits(invoice, opportunity, amount=100, org_amount=0, agreed_in_window=1)

    drift = compute_invoice_drift(invoice, base_url="https://staging.example.com/")
    assert drift.reconstructed_flw_amount == Decimal(100)
    assert drift.reconstruction_gap == Decimal(0)
    assert drift.late_delivery_units == 0
    assert drift.overbilled_units == 0
    assert drift.org_pay_drift == Decimal(0)
    assert drift.works[0].desired_deliveries_count == 1
    assert drift.invoice_url.startswith("https://staging.example.com")


def test_null_visit_that_was_billed_leaves_no_drift(opportunity):
    invoice = PaymentInvoiceFactory(
        opportunity=opportunity,
        amount=Decimal(100),  # the null-date visit WAS billed
        service_delivery=True,
        status=InvoiceStatus.PAID,
        end_date=END,
        date=END,
    )
    access = OpportunityAccessFactory(opportunity=opportunity)
    pu = PaymentUnitFactory(opportunity=opportunity, amount=100, org_amount=0)
    du = DeliverUnitFactory(payment_unit=pu, optional=False)
    cw = CompletedWorkFactory(opportunity_access=access, payment_unit=pu)
    cw.invoice = invoice
    cw.save()
    _approved_visit(cw, du, on=None, agree=True)

    drift = compute_invoice_drift(invoice)
    work = drift.works[0]
    assert work.desired_deliveries_count == 1
    assert work.reconstructed_deliveries_count == 1  # null assumed billed → counted
    assert work.late_delivery_units == 0
    assert work.legacy_null_status_date_visits == 1
    assert drift.reconstruction_gap == Decimal(0)  # reconstruction reproduces frozen
    assert drift.legacy_null_status_date_visits == 1


def test_gap_catches_null_visit_that_was_not_billed(opportunity):
    """When the "null was billed" assumption is wrong (the null visit was a genuine unbilled miss), folding
    over-counts the reconstruction so no unit drift shows — but reconstruction_gap goes negative and
    flags the invoice for review, with legacy_null_status_date_visits giving the reason."""
    invoice = PaymentInvoiceFactory(
        opportunity=opportunity,
        amount=Decimal(100),  # only the one dated visit was billed; the null visit was NOT
        service_delivery=True,
        status=InvoiceStatus.PAID,
        end_date=END,
        date=END,
    )
    access = OpportunityAccessFactory(opportunity=opportunity)
    pu = PaymentUnitFactory(opportunity=opportunity, amount=100, org_amount=0)
    du = DeliverUnitFactory(payment_unit=pu, optional=False)
    cw = CompletedWorkFactory(opportunity_access=access, payment_unit=pu)
    cw.invoice = invoice
    cw.save()
    _approved_visit(cw, du, on=IN_WINDOW, agree=True)  # billed
    _approved_visit(cw, du, on=None, agree=True)  # unbilled null → over-counted by the fold

    drift = compute_invoice_drift(invoice)
    work = drift.works[0]
    assert work.reconstructed_deliveries_count == 2  # both folded in
    assert work.late_delivery_units == 0  # unit drift hidden by the fold...
    assert drift.reconstruction_gap == Decimal(-100)  # ...but the gap catches it
    assert gap_is_unexplained(drift) is True
    assert drift.legacy_null_status_date_visits == 1


def test_late_delivery_drift_is_per_work(opportunity):
    invoice = PaymentInvoiceFactory(
        opportunity=opportunity,
        amount=Decimal(100),
        service_delivery=True,
        status=InvoiceStatus.PAID,
        end_date=END,
        date=END,
    )
    # 1 agreed in-window + 1 agreed only after end_date (orphaned) → desired 2, reconstructed 1 → +1 unit.
    _completed_work_with_visits(invoice, opportunity, amount=100, org_amount=0, agreed_in_window=1, agreed_late=1)

    drift = compute_invoice_drift(invoice)
    assert drift.works[0].reconstructed_deliveries_count == 1
    assert drift.works[0].desired_deliveries_count == 2
    assert drift.works[0].late_delivery_units == 1
    assert drift.works[0].late_approved_visit_count == 1
    assert drift.late_delivery_units == 1
    assert drift.late_delivery_drift == Decimal(100)
    assert drift.overbilled_units == 0
    assert drift.org_pay_drift == Decimal(0)


def test_overbilling_when_billed_visit_never_agreed(opportunity):
    # 1 approved-but-never-agreed visit → billed under old logic (reconstructed 1) but not owed now
    # (desired 0) → 1 unit over-billed = clawback candidate.
    invoice = PaymentInvoiceFactory(
        opportunity=opportunity,
        amount=Decimal(100),
        service_delivery=True,
        status=InvoiceStatus.PAID,
        end_date=END,
        date=END,
    )
    _completed_work_with_visits(invoice, opportunity, amount=100, org_amount=0, approved_only_in_window=1)

    drift = compute_invoice_drift(invoice)
    assert drift.works[0].reconstructed_deliveries_count == 1
    assert drift.works[0].desired_deliveries_count == 0
    assert drift.overbilled_units == 1
    assert drift.overbilled_drift == Decimal(100)
    assert drift.late_delivery_units == 0
    assert drift.works[0].late_approved_visit_count == 0


@pytest.mark.parametrize("frozen_amount", [Decimal(100), Decimal(120)])
def test_org_pay_drift_is_reported_separately(opportunity, frozen_amount):
    # flw=100, org=20. org_pay_drift is always the omitted workspace pay (Σ r*o) regardless of frozen.
    # reconstruction_gap distinguishes eras: 0 → FLW-only invoice (org truly missing);
    # ≈ org_pay_drift → the invoice already billed org (org not actually missing).
    invoice = PaymentInvoiceFactory(
        opportunity=opportunity,
        amount=frozen_amount,
        service_delivery=True,
        status=InvoiceStatus.PAID,
        end_date=END,
        date=END,
    )
    _completed_work_with_visits(invoice, opportunity, amount=100, org_amount=20, agreed_in_window=1)

    drift = compute_invoice_drift(invoice)
    assert drift.reconstructed_flw_amount == Decimal(100)
    assert drift.org_pay_drift == Decimal(20)
    assert drift.late_delivery_units == 0
    assert drift.overbilled_units == 0
    assert drift.reconstruction_gap == frozen_amount - Decimal(100)
    # Neither era counts as drift: residual is either 0 (FLW-era) or == org_pay_drift (org-era).
    assert not invoice_has_drift(drift)


def test_unexplained_residual_is_flagged(opportunity):
    # No org pay, 1 clean delivery → reconstructed_flw = 100, but frozen is 150. The 50 residual is
    # not explained by either era (org_pay_drift is 0), so the invoice must surface as an anomaly.
    invoice = PaymentInvoiceFactory(
        opportunity=opportunity,
        amount=Decimal(150),
        service_delivery=True,
        status=InvoiceStatus.PAID,
        end_date=END,
        date=END,
    )
    _completed_work_with_visits(invoice, opportunity, amount=100, org_amount=0, agreed_in_window=1)

    drift = compute_invoice_drift(invoice)
    assert drift.late_delivery_units == 0
    assert drift.overbilled_units == 0
    assert drift.org_pay_drift == Decimal(0)
    assert drift.reconstruction_gap == Decimal(50)
    assert invoice_has_drift(drift)


def test_visits_approved_between_end_date_and_generation_are_billed_not_late(opportunity):
    # Period ends Jan 31 (END) but the invoice was generated Feb 28 (GENERATED_ON). The one delivery was
    # approved+agreed Feb 15 — after end_date but before generation — so it WAS billed. The reconstruction
    # must count it (cutoff = generation date), yielding no late-delivery drift and a clean reconciliation.
    invoice = PaymentInvoiceFactory(
        opportunity=opportunity,
        amount=Decimal(100),
        service_delivery=True,
        status=InvoiceStatus.PAID,
        end_date=END,
        date=GENERATED_ON,
    )
    _completed_work_with_visits(invoice, opportunity, amount=100, org_amount=0, agreed_late=1)

    drift = compute_invoice_drift(invoice)
    assert drift.works[0].reconstructed_deliveries_count == 1  # counted via generation-date cutoff
    assert drift.works[0].desired_deliveries_count == 1
    assert drift.late_delivery_units == 0
    assert drift.reconstruction_gap == Decimal(0)


def test_overbilling_negative_drift_with_clean_reconciliation(opportunity):
    # Invoice honestly billed 2 units (frozen 200): 1 agreed + 1 approved-but-never-agreed, both
    # in-window. Old logic counts 2 (reconstruction reproduces the 200 → residual 0); current logic
    # owes only the agreed 1 → 1 unit over-billed = negative drift, with clean reconciliation.
    invoice = PaymentInvoiceFactory(
        opportunity=opportunity,
        amount=Decimal(200),
        service_delivery=True,
        status=InvoiceStatus.PAID,
        end_date=END,
        date=END,
    )
    _completed_work_with_visits(
        invoice, opportunity, amount=100, org_amount=0, agreed_in_window=1, approved_only_in_window=1
    )

    drift = compute_invoice_drift(invoice)
    assert drift.works[0].reconstructed_deliveries_count == 2
    assert drift.works[0].desired_deliveries_count == 1
    assert drift.reconstructed_flw_amount == Decimal(200)
    assert drift.reconstruction_gap == Decimal(0)
    assert drift.overbilled_units == 1
    assert drift.overbilled_drift == Decimal(100)
    assert drift.late_delivery_units == 0


def test_iter_and_summary_end_to_end(opportunity, tmp_path):
    invoice = PaymentInvoiceFactory(
        opportunity=opportunity,
        amount=Decimal(100),
        service_delivery=True,
        status=InvoiceStatus.PAID,
        end_date=END,
        date=END,
    )
    _completed_work_with_visits(
        invoice, opportunity, amount=100, org_amount=0, agreed_in_window=1, agreed_late=1
    )  # desired 2, reconstructed 1 → +1 late unit

    drifts = list(iter_invoice_drift(service_delivery_invoices()))
    assert len(drifts) == 1
    assert list(work_rows(drifts[0]))  # at least one work row

    invoice_out = tmp_path / "invoice_drift.csv"
    write_invoice_csv(drifts, str(invoice_out))
    invoice_contents = invoice_out.read_text()
    assert invoice.invoice_number in invoice_contents
    # Invoice-level report has exactly one data row (header + 1).
    assert len(invoice_contents.strip().splitlines()) == 2

    work_out = tmp_path / "work_drift.csv"
    write_work_csv(drifts, str(work_out))
    assert invoice.invoice_number in work_out.read_text()

    summary = "\n".join(summary_lines(drifts))
    assert "Invoices scanned: 1" in summary
