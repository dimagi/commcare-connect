import datetime
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal

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

    `invoiced_approved_count` decides what is billable; the dates only *scope* it:

    - first-billing works (`invoiced_approved_count == 0`) are scoped by the window, where their
      `status_modified_date` is meaningful;
    - late deltas (already partly billed) bypass the window entirely. A late duplicate keeps the
      work at `approved`, so its `status_modified_date` never moves off the original approval;
      windowing that stale date would silently defer a delta that must bill now.
    """
    if start_date is None or end_date is None:
        raise ValueError("start_date and end_date are required")

    return (
        billable_works_qs(opportunity)
        .filter(
            Q(invoiced_approved_count__gt=0)
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
class BillableRow:
    """One work's unbilled delta, priced and attributed to a month."""

    completed_work: CompletedWork
    billed_count: int
    month: datetime.date
    flw_pay: Money
    org_pay: Money
    exchange_rate: ExchangeRate

    @property
    def total_pay(self) -> Money:
        return self.flw_pay + self.org_pay


def _build_billable_rows(works, currency_code, end_date):
    """Price each billable work's delta into a `BillableRow`.

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
            BillableRow(
                completed_work=work,
                billed_count=billed_count,
                month=month,
                flw_pay=Money.from_local_amount(Decimal(billed_count * work.payment_unit.amount), rate),
                org_pay=Money.from_local_amount(Decimal(billed_count * work.payment_unit.org_amount), rate),
                exchange_rate=exchange_rate,
            )
        )
    return rows


@dataclass(frozen=True)
class LineItem:
    month: datetime.date
    payment_unit_name: str
    number_approved: int
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
                "flw_pay": Money.zero(),
                "org_pay": Money.zero(),
                "exchange_rate": row.exchange_rate.rate,
            },
        )
        group["number_approved"] += row.billed_count
        group["flw_pay"] += row.flw_pay
        group["org_pay"] += row.org_pay

    def display_order(entry):
        (month, _), group = entry
        return (month, group["payment_unit_name"])

    return [LineItem(month=month, **group) for (month, _), group in sorted(groups.items(), key=display_order)]


def get_billable_line_items(opportunity, start_date, end_date):
    works = get_billable_completed_works_qs(opportunity, start_date, end_date)
    return group_line_items(_build_billable_rows(works, opportunity.currency_code, end_date))


def get_invoice_line_items(invoice):
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

    return [
        LineItem(
            month=record["month"],
            payment_unit_name=record["payment_unit_name"],
            number_approved=record["number_approved"],
            flw_pay=Money(record["flw_local"], record["flw_usd"]),
            org_pay=Money(record["org_local"], record["org_usd"]),
            exchange_rate=record["rate"],
        )
        for record in records
    ]


@dataclass(frozen=True)
class DeliveryRow:
    completed_work: CompletedWork
    billed_count: int
    flw_pay: Money
    org_pay: Money

    @property
    def total_pay(self) -> Money:
        return self.flw_pay + self.org_pay


def get_invoice_delivery_rows(invoice):
    work_items = invoice.work_items.select_related(
        "completed_work__payment_unit__opportunity", "completed_work__opportunity_access__user"
    ).order_by("month", "completed_work__payment_unit__name")
    return [
        DeliveryRow(
            completed_work=item.completed_work,
            billed_count=item.billed_count,
            flw_pay=Money(item.flw_amount_local, item.flw_amount_usd),
            org_pay=Money(item.org_amount_local, item.org_amount_usd),
        )
        for item in work_items
    ]


def get_billable_delivery_rows(opportunity, start_date, end_date):
    works = get_billable_completed_works_qs(opportunity, start_date, end_date)
    return [
        DeliveryRow(
            completed_work=row.completed_work,
            billed_count=row.billed_count,
            flw_pay=row.flw_pay,
            org_pay=row.org_pay,
        )
        for row in _build_billable_rows(works, opportunity.currency_code, end_date)
    ]


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
    """First billing keeps the work's real approval month, so catch-up months stay accurate; a late
    delta takes the billing month, because its `status_modified_date` is frozen at the original
    approval (it only moves on a *status* change, and a late duplicate stays `approved`).
    """
    if work.invoiced_approved_count == 0 and work.status_modified_date:
        return get_month_start_date(work.status_modified_date)
    return get_month_start_date(end_date)
