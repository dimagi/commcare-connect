import datetime
import logging
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal

from django.db import transaction
from django.db.models import Exists, F, Max, OuterRef, Q, Sum
from django.db.models.functions import Coalesce

from commcare_connect.opportunity.models import (
    CompletedWork,
    CompletedWorkInvoice,
    CompletedWorkStatus,
    ExchangeRate,
)
from commcare_connect.utils.datetime import get_month_start_date

logger = logging.getLogger(__name__)

CENTS = Decimal("0.01")


def get_billable_completed_works_qs(opportunity, start_date, end_date):
    """Approved works with unbilled units.

    A work is billable when `saved_approved_count > invoiced_approved_count`.

    - First-time billing: the work's approval date is captured, so the work
      must fall within the invoice date window.

    - Subsequent billing: additional duplicate deliveries do not update the
      approval date. These works are billed in a subsequent invoice, as they
      arrived after the first billing and are not restricted by `start_date`.
      `end_date` still applies to prevent a new duplicate work from being billed
      before its first billing period.
    """
    if start_date is None or end_date is None:
        raise ValueError("start_date and end_date are required")

    return (
        billable_works_qs(opportunity)
        .filter(
            Q(has_first_billing=True, status_modified_date__date__lte=end_date)
            | Q(status_modified_date__date__gte=start_date, status_modified_date__date__lte=end_date)
        )
        .select_related("payment_unit__opportunity", "opportunity_access__user")
        .order_by("id")
    )


def billable_works_qs(opportunity):
    return CompletedWork.objects.filter(
        opportunity_access__opportunity=opportunity,
        status=CompletedWorkStatus.approved,
        saved_approved_count__gt=F("invoiced_approved_count"),
    ).annotate(
        has_first_billing=Exists(CompletedWorkInvoice.objects.filter(completed_work=OuterRef("pk"), is_delta=False))
    )


@dataclass(frozen=True)
class Money:
    """An amount in an opportunity's local currency together with its USD equivalent."""

    local: Decimal
    usd: Decimal

    @classmethod
    def zero(cls) -> "Money":
        return cls(Decimal(0), Decimal(0))

    @classmethod
    def from_local_amount(cls, local_amount: Decimal, exchange_rate: Decimal) -> "Money":
        # Match Django's DecimalField rounding when storing the USD amount.
        return cls(local=local_amount, usd=(local_amount / exchange_rate).quantize(CENTS, rounding=ROUND_HALF_EVEN))

    def __add__(self, other: "Money | int") -> "Money":
        if type(other) is int and other == 0:  # so a bare sum() can start from its implicit int 0
            return self
        if not isinstance(other, Money):
            return NotImplemented
        return Money(self.local + other.local, self.usd + other.usd)

    __radd__ = __add__


@dataclass(frozen=True)
class WorkPayRow:
    """One work's delta for a month, priced."""

    completed_work: CompletedWork
    billed_count: int
    month: datetime.date
    flw_pay: Money
    org_pay: Money
    exchange_rate: ExchangeRate
    is_delta: bool

    @property
    def total_pay(self) -> Money:
        return self.flw_pay + self.org_pay


def _build_billable_rows(works, currency_code, end_date):
    """Price each billable work's delta into a `WorkPayRow`.

    `end_date` serves two purposes: it bounds the first-billing works selected
    and determines the month to which a late delta is attributed.
    """
    rates_by_month = {}
    rows = []
    for work in works:
        month = _billed_month(work, end_date)
        if month not in rates_by_month:
            rates_by_month[month] = ExchangeRate.latest_exchange_rate(currency_code, month)
        exchange_rate = rates_by_month[month]

        billed_count = work.saved_approved_count - work.invoiced_approved_count
        rate = exchange_rate.rate
        rows.append(
            WorkPayRow(
                completed_work=work,
                billed_count=billed_count,
                month=month,
                flw_pay=Money.from_local_amount(Decimal(billed_count * work.payment_unit.amount), rate),
                org_pay=Money.from_local_amount(Decimal(billed_count * work.payment_unit.org_amount), rate),
                exchange_rate=exchange_rate,
                is_delta=work.has_first_billing,
            )
        )
    return rows


@dataclass(frozen=True)
class LineItem:
    month: datetime.date
    payment_unit_name: str
    number_approved: int
    late_delta_units: int
    flw_pay: Money
    org_pay: Money
    exchange_rate: Decimal

    @property
    def total_pay(self) -> Money:
        return self.flw_pay + self.org_pay


def group_line_items(rows):
    groups = {}
    for row in rows:
        payment_unit = row.completed_work.payment_unit
        group = groups.setdefault(
            (row.month, payment_unit.id),
            {
                "payment_unit_name": payment_unit.name,
                "number_approved": 0,
                "late_delta_units": 0,
                "flw_pay": Money.zero(),
                "org_pay": Money.zero(),
                "exchange_rate": row.exchange_rate.rate,
            },
        )
        group["number_approved"] += row.billed_count
        if row.is_delta:
            group["late_delta_units"] += row.billed_count
        group["flw_pay"] += row.flw_pay
        group["org_pay"] += row.org_pay

    def display_order(entry):
        (month, _), group = entry
        return (month, group["payment_unit_name"])

    return [LineItem(month=month, **group) for (month, _), group in sorted(groups.items(), key=display_order)]


def total_late_delta_units(line_items):
    """Additional deliveries billed here for work that an earlier invoice already billed."""
    return sum(item.late_delta_units for item in line_items)


def get_billable_line_items(opportunity, start_date, end_date):
    works = get_billable_completed_works_qs(opportunity, start_date, end_date)
    return group_line_items(_build_billable_rows(works, opportunity.currency_code, end_date))


def get_invoice_line_items(invoice):
    records = (
        invoice.work_items.values("completed_work__payment_unit", "month")
        .annotate(
            payment_unit_name=F("completed_work__payment_unit__name"),
            number_approved=Sum("billed_count"),
            late_delta_units=Coalesce(Sum("billed_count", filter=Q(is_delta=True)), 0),
            flw_local=Sum("flw_amount_local"),
            org_local=Sum("org_amount_local"),
            flw_usd=Sum("flw_amount_usd"),
            org_usd=Sum("org_amount_usd"),
            # Every row in a (month, currency) group was priced at the same rate; Max collapses them.
            rate=Max("exchange_rate__rate"),
        )
        .order_by("month", "payment_unit_name")
    )

    return [
        LineItem(
            month=record["month"],
            payment_unit_name=record["payment_unit_name"],
            number_approved=record["number_approved"],
            late_delta_units=record["late_delta_units"],
            flw_pay=Money(record["flw_local"], record["flw_usd"]),
            org_pay=Money(record["org_local"], record["org_usd"]),
            exchange_rate=record["rate"],
        )
        for record in records
    ]


def get_invoice_delivery_rows_for_export(invoice):
    work_items = invoice.work_items.select_related(
        "completed_work__payment_unit__opportunity", "completed_work__opportunity_access__user", "exchange_rate"
    ).order_by("month", "completed_work__payment_unit__name")
    return [
        WorkPayRow(
            completed_work=item.completed_work,
            billed_count=item.billed_count,
            month=item.month,
            flw_pay=Money(item.flw_amount_local, item.flw_amount_usd),
            org_pay=Money(item.org_amount_local, item.org_amount_usd),
            exchange_rate=item.exchange_rate,
            is_delta=item.is_delta,
        )
        for item in work_items
    ]


def get_billable_delivery_rows_for_export(opportunity, start_date, end_date):
    works = get_billable_completed_works_qs(opportunity, start_date, end_date)
    return _build_billable_rows(works, opportunity.currency_code, end_date)


def bill_invoice(invoice, start_date, end_date):
    """Freeze this invoice's line items and advance each work's invoiced count.

    `invoiced_approved_count` is only ever advanced here. `invoice` may be unsaved: it is written by
    `_freeze_line_items`, and only if there is a delta to bill, so a caller with nothing billable
    gets `[]` back and no invoice row.
    """
    opportunity = invoice.opportunity
    with transaction.atomic():
        works = get_billable_completed_works_qs(opportunity, start_date, end_date).select_for_update(of=("self",))
        rows = _build_billable_rows(works, opportunity.currency_code, end_date)
        if not rows:
            return []

        _freeze_line_items(invoice, rows)

    return rows


def rollback_invoice_line_items(invoice):
    with transaction.atomic():
        billed_by_work = dict(invoice.work_items.values_list("completed_work_id", "billed_count"))
        if not billed_by_work:
            return

        works = []
        for work in CompletedWork.objects.select_for_update(of=("self",)).filter(id__in=billed_by_work):
            work_billed_count = billed_by_work[work.id]
            if work_billed_count > work.invoiced_approved_count:
                # Only reachable if the watermark and the rows have already diverged. Clamping keeps
                # the cancel working, but the divergence itself is a bug worth seeing.
                logger.error(
                    "Invoice %s releases %s units of completed work %s but only %s are invoiced; clamping to 0.",
                    invoice.id,
                    work_billed_count,
                    work.id,
                    work.invoiced_approved_count,
                )
            work.invoiced_approved_count = max(0, work.invoiced_approved_count - work_billed_count)
            works.append(work)
        CompletedWork.objects.bulk_update(works, ["invoiced_approved_count"])
        invoice.work_items.all().delete()


def _freeze_line_items(invoice, rows):
    # For display only: show the latest billed month's rate on the invoice.
    invoice.exchange_rate = ExchangeRate.latest_exchange_rate(
        invoice.opportunity.currency_code, max(row.month for row in rows)
    )
    total = sum(row.total_pay for row in rows)
    invoice.amount = total.local
    invoice.amount_usd = total.usd
    invoice.save()

    CompletedWorkInvoice.objects.bulk_create(
        [
            CompletedWorkInvoice(
                invoice=invoice,
                completed_work=row.completed_work,
                billed_count=row.billed_count,
                month=row.month,
                flw_amount_local=row.flw_pay.local,
                flw_amount_usd=row.flw_pay.usd,
                org_amount_local=row.org_pay.local,
                org_amount_usd=row.org_pay.usd,
                exchange_rate=row.exchange_rate,
                is_delta=row.is_delta,
            )
            for row in rows
        ]
    )

    billed_works = []
    for row in rows:
        work = row.completed_work
        work.invoiced_approved_count = work.saved_approved_count
        billed_works.append(work)
    CompletedWork.objects.bulk_update(billed_works, ["invoiced_approved_count"], batch_size=500)


def _billed_month(work, end_date):
    """A first billing keeps the work's real approval month, so catch-up months stay accurate; a
    late delta takes the billing month, because its `status_modified_date` is frozen at the original
    approval (it only moves on a *status* change, and a late duplicate stays `approved`).
    """
    if not work.has_first_billing and work.status_modified_date:
        return get_month_start_date(work.status_modified_date)
    return get_month_start_date(end_date)
