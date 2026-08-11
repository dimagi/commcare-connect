import datetime
import secrets

from django.db.models import Count, Min, Q
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from commcare_connect.opportunity.models import InvoiceStatus
from commcare_connect.opportunity.utils.invoice_line_items import billable_works_qs
from commcare_connect.utils.datetime import get_end_date_previous_month, get_month_start_date


def get_start_date_for_invoice(opportunity):
    """Return the invoice window start.

    Use the earliest unbilled approval for works awaiting a first billing. If only late deltas
    are billable, use the invoiced month.
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
