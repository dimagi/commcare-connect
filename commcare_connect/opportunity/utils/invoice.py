import datetime
import secrets

from django.db.models import Min
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from commcare_connect.opportunity.models import CompletedWork, CompletedWorkStatus, InvoiceStatus


def get_start_date_for_invoice(opportunity):
    date = (
        CompletedWork.objects.filter(
            invoice__isnull=True,
            opportunity_access__opportunity=opportunity,
            status=CompletedWorkStatus.approved,
        )
        .aggregate(earliest_date=Min("status_modified_date"))
        .get("earliest_date")
    )

    if date:
        start_date = date.date()
    else:
        start_date = opportunity.start_date

    return start_date.replace(day=1)


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
    def validate_transition(cls, current_status, new_status, is_program_manager):
        if not cls.is_transition_allowed(current_status, new_status):
            return False, _(
                "Invalid status transition. Current status: '%(current)s'. Cannot change to: '%(new)s'."
            ) % {"current": InvoiceStatus.get_label(current_status), "new": InvoiceStatus.get_label(new_status)}
        if not cls.can_role_perform_action(is_program_manager, new_status):
            return False, _("You do not have permission to perform this action.")
        return True, None

    @classmethod
    def is_transition_allowed(cls, current_status, new_status):
        allowed_statuses = cls.ALLOWED_STATUS_TRANSITIONS.get(current_status, set())
        return new_status in allowed_statuses

    @classmethod
    def can_role_perform_action(cls, is_program_manager, new_status):
        role = "program_manager" if is_program_manager else "network_manager"
        allowed_for_role = cls.ROLE_ALLOWED_STATUSES.get(role, set())
        return new_status in allowed_for_role

    @classmethod
    def get_status_update_message(cls, new_status, invoice_number):
        return cls.STATUS_UPDATE_MESSAGES.get(new_status, _("Invoice %(invoice_number)s status has been updated.")) % {
            "invoice_number": invoice_number
        }
