import datetime
from dataclasses import dataclass
from decimal import Decimal

from django.db import transaction
from django.db.models import F, Max, Q, Sum

from commcare_connect.opportunity.models import (
    CompletedWork,
    CompletedWorkInvoice,
    CompletedWorkStatus,
    ExchangeRate,
)
from commcare_connect.utils.datetime import get_month_start_date

CENTS = Decimal("0.01")


def get_billable_completed_works_qs(opportunity, start_date, end_date):
    """Approved works that still have unbilled units.

    The watermark decides what is billable; the dates only *scope* it:

    - first-billing works (`invoiced_approved_count == 0`) are scoped by the window, where their
      `status_modified_date` is meaningful;
    - late deltas (already partly billed) bypass the window entirely. A late duplicate keeps the
      work at `approved`, so its `status_modified_date` never moves off the original approval;
      windowing that stale date would silently defer a delta that must bill now.
    """
    if start_date is None or end_date is None:
        raise ValueError("start_date and end_date are required")

    return CompletedWork.objects.filter(
        opportunity_access__opportunity=opportunity,
        status=CompletedWorkStatus.approved,
        saved_approved_count__gt=F("invoiced_approved_count"),
    ).filter(
        Q(invoiced_approved_count__gt=0)
        | Q(status_modified_date__date__gte=start_date, status_modified_date__date__lte=end_date)
    )


@dataclass(frozen=True)
class BillableRow:
    """One work's unbilled delta, priced and attributed to a month."""

    completed_work: CompletedWork
    billed_count: int
    month: datetime.date
    flw_amount_local: Decimal
    flw_amount_usd: Decimal
    org_amount_local: Decimal
    org_amount_usd: Decimal
    exchange_rate: ExchangeRate

    @property
    def total_amount_local(self):
        return self.flw_amount_local + self.org_amount_local

    @property
    def total_amount_usd(self):
        return self.flw_amount_usd + self.org_amount_usd


def _build_billable_rows(opportunity, start_date, end_date, for_update=False):
    """Price each billable work's delta into a BillableRow.

    `end_date` is the billing cutoff — the invoice's end date — and carries two roles: it bounds
    which first-billing works are selected, and it is the month a late delta is attributed to (a
    late delta bypasses the bound but still needs a month to be billed under).

    for_update=True is only for writes. Read-only callers must leave it False.
    """
    works = get_billable_completed_works_qs(opportunity, start_date, end_date).select_related(
        "payment_unit__opportunity", "opportunity_access__user"
    )
    if for_update:
        works = works.select_for_update(of=("self",))

    currency_code = opportunity.currency_code
    rates_by_month = {}
    rows = []
    for work in works.order_by("id"):
        month = _billed_month(work, end_date)
        if month not in rates_by_month:
            rates_by_month[month] = ExchangeRate.latest_exchange_rate(currency_code, month)
        exchange_rate = rates_by_month[month]

        billed_count = work.saved_approved_count - work.invoiced_approved_count
        flw_local = Decimal(billed_count * work.payment_unit.amount)
        org_local = Decimal(billed_count * work.payment_unit.org_amount)
        rows.append(
            BillableRow(
                completed_work=work,
                billed_count=billed_count,
                month=month,
                flw_amount_local=flw_local,
                flw_amount_usd=_to_usd(flw_local, exchange_rate.rate),
                org_amount_local=org_local,
                org_amount_usd=_to_usd(org_local, exchange_rate.rate),
                exchange_rate=exchange_rate,
            )
        )
    return rows


def group_line_items(rows, currency_code):
    groups = {}
    for row in rows:
        payment_unit = row.completed_work.payment_unit
        group = groups.setdefault(
            (row.month, payment_unit.id),
            {
                "payment_unit_name": payment_unit.name,
                "number_approved": 0,
                "flw_amount_local": Decimal(0),
                "org_amount_local": Decimal(0),
                "flw_amount_usd": Decimal(0),
                "org_amount_usd": Decimal(0),
                "exchange_rate": row.exchange_rate.rate,
            },
        )
        group["number_approved"] += row.billed_count
        group["flw_amount_local"] += row.flw_amount_local
        group["org_amount_local"] += row.org_amount_local
        group["flw_amount_usd"] += row.flw_amount_usd
        group["org_amount_usd"] += row.org_amount_usd

    def display_order(entry):
        (month, _), group = entry
        return (month, group["payment_unit_name"])

    return [
        _line_item(month=month, currency_code=currency_code, **group)
        for (month, _), group in sorted(groups.items(), key=display_order)
    ]


def get_billable_line_items(opportunity, start_date, end_date):
    """The line items an invoice over this window would bill, computed from live state.
    Use `get_invoice_line_items` for an invoice that has already been issued;
    that reads frozen rows and cannot move.
    """
    rows = _build_billable_rows(opportunity, start_date, end_date)
    return group_line_items(rows, opportunity.currency_code)


def get_invoice_line_items(invoice):
    """This invoice's line items *as billed*, aggregated over its frozen snapshot rows."""
    records = (
        invoice.work_items.values("completed_work__payment_unit", "month")
        .annotate(
            payment_unit_name=F("completed_work__payment_unit__name"),
            number_approved=Sum("billed_count"),
            flw_local=Sum("flw_amount_local"),
            org_local=Sum("org_amount_local"),
            flw_usd=Sum("flw_amount_usd"),
            org_usd=Sum("org_amount_usd"),
            # Every row in a (month, currency) group was priced at the same rate; Max collapses them.
            rate=Max("exchange_rate__rate"),
        )
        .order_by("month", "payment_unit_name")
    )

    currency_code = invoice.opportunity.currency_code
    return [
        _line_item(
            month=record["month"],
            payment_unit_name=record["payment_unit_name"],
            number_approved=record["number_approved"],
            flw_amount_local=record["flw_local"],
            org_amount_local=record["org_local"],
            flw_amount_usd=record["flw_usd"],
            org_amount_usd=record["org_usd"],
            exchange_rate=record["rate"],
            currency_code=currency_code,
        )
        for record in records
    ]


def get_invoice_delivery_rows(invoice):
    """Per-delivery export rows for an issued invoice, read from its frozen snapshot rows."""
    work_items = invoice.work_items.select_related(
        "completed_work__payment_unit__opportunity", "completed_work__opportunity_access__user"
    )
    return [
        _delivery_row(
            completed_work=item.completed_work,
            billed_count=item.billed_count,
            flw_amount_local=item.flw_amount_local,
            org_amount_local=item.org_amount_local,
            total_amount_usd=item.flw_amount_usd + item.org_amount_usd,
        )
        for item in work_items
    ]


def get_billable_delivery_rows(opportunity, start_date, end_date):
    """Per-delivery export rows for a window that has not been invoiced yet, computed from live state."""
    return [
        _delivery_row(
            completed_work=row.completed_work,
            billed_count=row.billed_count,
            flw_amount_local=row.flw_amount_local,
            org_amount_local=row.org_amount_local,
            total_amount_usd=row.total_amount_usd,
        )
        for row in _build_billable_rows(opportunity, start_date, end_date)
    ]


def bill_invoice(invoice, start_date, end_date):
    """Bill an invoice that has to exist afterwards either way: `create_invoice_line_items`, except
    that a period with no delta still leaves a saved invoice, for zero.

    Only whoever created the invoice knows whether that is right. The automated task calls
    `create_invoice_line_items` directly, so an empty period leaves nothing behind at all.
    """
    rows = create_invoice_line_items(invoice, start_date, end_date)
    if not rows:
        invoice.amount = 0
        invoice.amount_usd = 0
        invoice.save()
    return rows


def create_invoice_line_items(invoice, start_date, end_date):
    """Freeze this invoice's line items and advance the billed-work watermark.

    `invoiced_approved_count` is only ever advanced here.

    The invoice's totals always come from the rows this froze: it derived `amount`, `amount_usd`
    and `exchange_rate` from the same locked read that wrote them, so nothing else may set them.
    With no delta nothing at all is written — not even `invoice` itself, if it is still unsaved,
    which is how the automated task avoids creating an invoice it would have to delete. Callers
    that need the invoice saved regardless go through `bill_invoice`.
    """
    with transaction.atomic():
        rows = _build_billable_rows(invoice.opportunity, start_date, end_date, for_update=True)
        if not rows:
            return []

        _freeze_line_items(invoice, rows)

    return rows


def rollback_invoice_line_items(invoice):
    """Undo this invoice's billing for cancelled/rejected invoices.

    Reduce each work item's watermark by the amount billed by this invoice, not to zero.
    Other invoices covering the same work must keep their billed portion.
    """
    with transaction.atomic():
        billed_by_work = dict(invoice.work_items.values_list("completed_work_id", "billed_count"))
        if not billed_by_work:
            return

        works = []
        for work in CompletedWork.objects.select_for_update(of=("self",)).filter(id__in=billed_by_work):
            work.invoiced_approved_count = max(0, work.invoiced_approved_count - billed_by_work[work.id])
            works.append(work)
        CompletedWork.objects.bulk_update(works, ["invoiced_approved_count"])
        invoice.work_items.all().delete()


def _freeze_line_items(invoice, rows):
    """Write `rows` as this invoice's snapshot, advance each work's watermark, and set the fields
    that are derived from what was billed: `exchange_rate`, `amount`, `amount_usd`.

    Unsafe to call directly: it assumes an open transaction and rows built with `for_update=True`.
    """
    # The latest month actually billed, not the window's end: works approved only in May take May's
    # rate even when the window runs to June 30.
    invoice.exchange_rate = ExchangeRate.latest_exchange_rate(
        invoice.opportunity.currency_code, max(row.month for row in rows)
    )
    invoice.amount = sum(row.total_amount_local for row in rows)
    invoice.amount_usd = sum(row.total_amount_usd for row in rows)
    invoice.save()

    CompletedWorkInvoice.objects.bulk_create(
        [
            CompletedWorkInvoice(
                invoice=invoice,
                completed_work=row.completed_work,
                month=row.month,
                billed_count=row.billed_count,
                flw_amount_local=row.flw_amount_local,
                flw_amount_usd=row.flw_amount_usd,
                org_amount_local=row.org_amount_local,
                org_amount_usd=row.org_amount_usd,
                exchange_rate=row.exchange_rate,
            )
            for row in rows
        ]
    )

    billed_works = []
    for row in rows:
        work = row.completed_work
        work.invoiced_approved_count = work.saved_approved_count
        billed_works.append(work)
    # batch_size caps the CASE arms per statement; Postgres would otherwise emit one UPDATE
    # with a branch per work, which parses slowly on a large invoice.
    CompletedWork.objects.bulk_update(billed_works, ["invoiced_approved_count"], batch_size=500)


def _billed_month(work, end_date):
    """First billing keeps the work's real approval month, so catch-up months stay accurate; a late
    delta takes the billing month, because its `status_modified_date` is frozen at the original
    approval (it only moves on a *status* change, and a late duplicate stays `approved`).
    """
    if work.invoiced_approved_count == 0 and work.status_modified_date:
        return get_month_start_date(work.status_modified_date)
    return get_month_start_date(end_date)


def _to_usd(amount_local: Decimal, exchange_rate: Decimal) -> Decimal:
    """Round each row to cents before summing so `invoice.amount_usd` always equals
    the sum of the stored rows. Although `DecimalField(decimal_places=2)` rounds on
    save, leaving values at full precision in memory would allow invoice totals to
    drift from persisted line items.

    Keep the default `ROUND_HALF_EVEN` to match Django's `DecimalField`
    quantization and existing backfilled rows.
    """
    return (amount_local / exchange_rate).quantize(CENTS)


def _delivery_row(completed_work, billed_count, flw_amount_local, org_amount_local, total_amount_usd):
    return {
        "payment_unit": completed_work.payment_unit.name,
        "opportunity": completed_work.payment_unit.opportunity.name,
        "entity_name": completed_work.entity_name,
        "username": completed_work.opportunity_access.user.name,
        "date_created": completed_work.date_created,
        "date_approved": completed_work.status_modified_date,
        "approved_count": billed_count,
        "flw_amount_local": flw_amount_local,
        "org_amount_local": org_amount_local,
        "total_amount_local": flw_amount_local + org_amount_local,
        "total_amount_usd": total_amount_usd,
    }


def _line_item(
    month,
    payment_unit_name,
    number_approved,
    flw_amount_local,
    org_amount_local,
    flw_amount_usd,
    org_amount_usd,
    exchange_rate,
    currency_code,
):
    return {
        "month": month,
        "payment_unit_name": payment_unit_name,
        "number_approved": number_approved,
        "flw_amount_local": flw_amount_local,
        "org_amount_local": org_amount_local,
        "total_amount_local": flw_amount_local + org_amount_local,
        "flw_amount_usd": flw_amount_usd,
        "org_amount_usd": org_amount_usd,
        "total_amount_usd": flw_amount_usd + org_amount_usd,
        "exchange_rate": exchange_rate,
        "currency": currency_code,
    }
