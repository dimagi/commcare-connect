from django.utils.translation import gettext as _

# Server/Web events
INVITE_SENT = "invite_sent"
RECORDS_APPROVED = "records_approved"
RECORDS_REJECTED = "records_rejected"
PAYMENT_APPROVED = "payment_approved"
PAYMENT_ACCRUED = "payment_accrued"
PAYMENT_TRANSFERRED = "payment_transferred"
NOTIFICATIONS_SENT = "notifications_sent"
ADDITIONAL_BUDGET_ADDED = "additional_budget_added"

EVENT_TYPES = {
    INVITE_SENT: _("Invite Sent"),
    RECORDS_APPROVED: _("Records Approved"),
    RECORDS_REJECTED: _("Records Rejected"),
    PAYMENT_APPROVED: _("Payment Approved"),
    PAYMENT_ACCRUED: _("Payment Accrued"),
    PAYMENT_TRANSFERRED: _("Payment Transferred"),
    NOTIFICATIONS_SENT: _("Notifications Sent"),
    ADDITIONAL_BUDGET_ADDED: _("Additional Budget Added"),
}


EVENT_TYPE_CHOICES = list(EVENT_TYPES.items())


# Inferred Events
RECORDS_FLAGGED = "records_flagged"
