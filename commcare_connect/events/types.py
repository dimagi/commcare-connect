from django.utils.translation import gettext as _

# Server/Web events
INVITE_SENT = _("Invite Sent")
RECORDS_APPROVED = _("Records Approved")
RECORDS_REJECTED = _("Records Rejected")
PAYMENT_APPROVED = _("Payment Approved")
PAYMENT_ACCRUED = _("Payment Accrued")
PAYMENT_TRANSFERRED = _("Payment Transferred")
NOTIFICATIONS_SENT = _("Notifications Sent")
ADDITIONAL_BUDGET_ADDED = _("Additional Budget Added")


# Inferred Events
RECORDS_FLAGGED = _("Records Flagged")

EVENT_TYPES = [
    INVITE_SENT,
    RECORDS_APPROVED,
    RECORDS_REJECTED,
    PAYMENT_APPROVED,
    PAYMENT_ACCRUED,
    PAYMENT_TRANSFERRED,
    NOTIFICATIONS_SENT,
    ADDITIONAL_BUDGET_ADDED,
    RECORDS_FLAGGED,
]
