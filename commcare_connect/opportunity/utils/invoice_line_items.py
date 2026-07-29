import datetime
from dataclasses import dataclass
from decimal import Decimal

from django.db import transaction
from django.db.models import F, Q

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
    exchange_rate: Decimal | int  # get_exchange_rate returns int 1 for USD; the DecimalField coerces

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
    from commcare_connect.opportunity.visit_import import get_exchange_rate

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
            rates_by_month[month] = get_exchange_rate(currency_code, month)
        rate = rates_by_month[month]

        billed_count = work.saved_approved_count - work.invoiced_approved_count
        flw_local = Decimal(billed_count * work.payment_unit.amount)
        org_local = Decimal(billed_count * work.payment_unit.org_amount)
        rows.append(
            BillableRow(
                completed_work=work,
                billed_count=billed_count,
                month=month,
                flw_amount_local=flw_local,
                flw_amount_usd=_to_usd(flw_local, rate),
                org_amount_local=org_local,
                org_amount_usd=_to_usd(org_local, rate),
                exchange_rate=rate,
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
                "exchange_rate": row.exchange_rate,
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


def create_invoice_line_items(invoice, start_date, end_date):
    """Freeze this invoice's line items and advance the billed-work watermark.

    `invoiced_approved_count` is only ever advanced here. `invoice` may be unsaved: it is written by
    `_freeze_line_items`, and only if there is a delta to bill, so a caller with nothing billable
    gets `[]` back and no invoice row.
    """
    with transaction.atomic():
        rows = _build_billable_rows(invoice.opportunity, start_date, end_date, for_update=True)
        if not rows:
            return []

        _freeze_line_items(invoice, rows)

    return rows


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


def _to_usd(amount_local: Decimal, exchange_rate: int | Decimal) -> Decimal:
    """Round each row to cents before summing so `invoice.amount_usd` always equals
    the sum of the stored rows. Although `DecimalField(decimal_places=2)` rounds on
    save, leaving values at full precision in memory would allow invoice totals to
    drift from persisted line items.

    Keep the default `ROUND_HALF_EVEN` to match Django's `DecimalField`
    quantization and existing backfilled rows.
    """
    return (amount_local / exchange_rate).quantize(CENTS)


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
