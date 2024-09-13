from django.utils.translation import gettext as _

# Server/Web events
INVITE_SENT = _("Invite Sent")
INVITE_ACCEPTED = _("Invite Accepted")
RECORDS_APPROVED = _("Records Approved")
RECORDS_REJECTED = _("Records Rejected")
PAYMENT_APPROVED = _("Payment Approved")
PAYMENT_ACCRUED = _("Payment Accrued")
PAYMENT_TRANSFERRED = _("Payment Transferred")
NOTIFICATIONS_SENT = _("Notifications Sent")
ADDITIONAL_BUDGET_ADDED = _("Additional Budget Added")

MODULE_COMPLETED = _("Module Completed")
ALL_MODULES_COMPLETED = _("All Modules Completed")
ASSESSMENT_PASSED = _("Assessment Passed")
ASSESSMENT_FAILED = _("Assessment Failed")

JOB_CLAIMED = _("Job Claimed Successfully")
DELIVERY_FORM_SUBMITTED = _("Delivery Form Submitted")
PAYMENT_ACKNOWLEDGED = _("Payment Acknowledged")


EVENT_TYPES = [
    INVITE_SENT,
    INVITE_ACCEPTED,
    JOB_CLAIMED,
    MODULE_COMPLETED,
    ALL_MODULES_COMPLETED,
    ASSESSMENT_PASSED,
    ASSESSMENT_FAILED,
    DELIVERY_FORM_SUBMITTED,
    RECORDS_APPROVED,
    RECORDS_REJECTED,
    PAYMENT_APPROVED,
    PAYMENT_ACCRUED,
    PAYMENT_TRANSFERRED,
    PAYMENT_ACKNOWLEDGED,
    NOTIFICATIONS_SENT,
    ADDITIONAL_BUDGET_ADDED,
]
