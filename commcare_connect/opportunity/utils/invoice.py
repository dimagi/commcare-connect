import calendar
import datetime
import secrets

from django.db.models import Count, Min, Q
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from commcare_connect.opportunity.models import InvoiceStatus
from commcare_connect.opportunity.utils.invoice_line_items import billable_works_qs
from commcare_connect.utils.datetime import get_end_date_previous_month, get_month_series, get_month_start_date


def get_start_date_for_invoice(opportunity):
    """Return the invoice window start.

    Use the earliest unbilled approval for works awaiting first billing.
    If only late deltas are billable, use the previous month as the start date.
    """
    aggregates = billable_works_qs(opportunity).aggregate(
        first_billing_date=Min("status_modified_date", filter=Q(has_first_billing=False)),
        late_delta_count=Count("id", filter=Q(has_first_billing=True)),
    )

    if aggregates["first_billing_date"]:
        start_date = aggregates["first_billing_date"]
    elif aggregates["late_delta_count"]:
        # No first billing pending: only late deltas remain, and a late delta bills under the month
        # being invoiced — the *previous* month, since that is the window the automated invoicing process uses.
        start_date = get_end_date_previous_month()
    else:
        # Nothing billable at all, preserves existing logic
        start_date = opportunity.start_date

    return get_month_start_date(start_date)


def get_end_date_for_invoice(start_date):
    last_day_previous_month = datetime.date.today().replace(day=1) - datetime.timedelta(days=1)

    if start_date > last_day_previous_month:
        return datetime.date.today() - datetime.timedelta(days=1)
    return last_day_previous_month


def parse_invoice_month(value):
    """Parse a `YYYY-MM` query value into the first day of that month, or None if absent or malformed."""
    if not value:
        return None
    try:
        return datetime.datetime.strptime(value, "%Y-%m").date()
    except ValueError:
        return None


def filter_invoices_by_month(queryset, month_start):
    """Invoices that belong to the calendar month starting on `month_start`.

    A service delivery invoice belongs to every month its billing period overlaps, so an invoice
    spanning several months is listed under each of them. A custom invoice belongs to the month
    of its expense date. When those dates are missing the generation date decides.
    """
    month_end = month_start.replace(day=calendar.monthrange(month_start.year, month_start.month)[1])
    month = (month_start, month_end)

    period_overlaps = Q(
        service_delivery=True,
        start_date__isnull=False,
        end_date__isnull=False,
        start_date__lte=month_end,
        end_date__gte=month_start,
    )
    period_missing = Q(service_delivery=True) & (Q(start_date__isnull=True) | Q(end_date__isnull=True))
    expense_in_month = Q(service_delivery=False, date_of_expense__range=month)
    expense_missing = Q(service_delivery=False, date_of_expense__isnull=True)
    generated_in_month = Q(date__range=month)

    return queryset.filter(
        period_overlaps | expense_in_month | ((period_missing | expense_missing) & generated_in_month)
    )


def get_invoice_month_options(queryset):
    """The months that list at least one invoice in `queryset`, newest first."""
    months = set()
    rows = queryset.values("service_delivery", "start_date", "end_date", "date_of_expense", "date")
    for row in rows:
        months.update(_invoice_months(row))
    return sorted(months, reverse=True)


def _invoice_months(row):
    """Python mirror of the `filter_invoices_by_month` rule, for one invoice's date fields."""
    if row["service_delivery"] and row["start_date"] and row["end_date"]:
        return get_month_series(get_month_start_date(row["start_date"]), get_month_start_date(row["end_date"]))
    if not row["service_delivery"] and row["date_of_expense"]:
        return [get_month_start_date(row["date_of_expense"])]
    return [get_month_start_date(row["date"])]


def resolve_invoice_month(month_param, month_options, highlight=None):
    """Which month the invoice list shows: the requested one, everything for `all` or a highlight
    link, otherwise the most recent month. None means all months."""
    if month_param == "all" or highlight:
        return None
    requested = parse_invoice_month(month_param)
    if requested:
        return requested
    return month_options[0] if month_options else None


def generate_invoice_number():
    return secrets.token_hex(5).upper()


class InvoiceWorkflow:
    """Domain workflow rules for invoice status transitions."""

    ALLOWED_STATUS_TRANSITIONS = {
        InvoiceStatus.PENDING_NM_REVIEW: {
            InvoiceStatus.PENDING_PM_REVIEW,
            InvoiceStatus.CANCELLED_BY_NM,
        },
        InvoiceStatus.PENDING_PM_REVIEW: {
            InvoiceStatus.READY_TO_PAY,
            InvoiceStatus.REJECTED_BY_PM,
        },
        InvoiceStatus.READY_TO_PAY: {
            InvoiceStatus.REJECTED_BY_PM,
        },
    }

    ROLE_ALLOWED_STATUSES = {
        "network_manager": {
            InvoiceStatus.PENDING_PM_REVIEW,
            InvoiceStatus.CANCELLED_BY_NM,
        },
        "program_manager": {
            InvoiceStatus.READY_TO_PAY,
            InvoiceStatus.REJECTED_BY_PM,
        },
    }

    STATUS_UPDATE_MESSAGES = {
        InvoiceStatus.PENDING_PM_REVIEW: gettext_lazy("Invoice %(invoice_number)s has been submitted for approval."),
        InvoiceStatus.CANCELLED_BY_NM: gettext_lazy(
            "Invoice %(invoice_number)s has been cancelled by Network Manager."
        ),
        InvoiceStatus.READY_TO_PAY: gettext_lazy("Invoice %(invoice_number)s has been approved and is ready to pay."),
        InvoiceStatus.REJECTED_BY_PM: gettext_lazy("Invoice %(invoice_number)s has been rejected by Program Manager."),
    }

    @classmethod
    def validate_transition(cls, current_status, new_status, role):
        if not cls.is_transition_allowed(current_status, new_status):
            return False, _(
                "Invalid status transition. Current status: '%(current)s'. Cannot change to: '%(new)s'."
            ) % {"current": InvoiceStatus.get_label(current_status), "new": InvoiceStatus.get_label(new_status)}
        if not cls.can_role_perform_action(role, new_status):
            return False, _("You do not have permission to perform this action.")
        return True, None

    @classmethod
    def is_transition_allowed(cls, current_status, new_status):
        allowed_statuses = cls.ALLOWED_STATUS_TRANSITIONS.get(current_status, set())
        return new_status in allowed_statuses

    @classmethod
    def can_role_perform_action(cls, role, new_status):
        allowed_for_role = cls.ROLE_ALLOWED_STATUSES.get(role, set())
        return new_status in allowed_for_role

    @classmethod
    def get_status_update_message(cls, new_status, invoice_number):
        return cls.STATUS_UPDATE_MESSAGES.get(new_status, _("Invoice %(invoice_number)s status has been updated.")) % {
            "invoice_number": invoice_number
        }
