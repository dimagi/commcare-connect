"""The Finance payment request for an invoice: a ticket on the Project Finance Jira service desk.

Connect raises "Project Finance - Request to Process and Pay an External Invoice/Bill" (FIN request
type 186). Almost every field on it is a question on an attached Jira Form, answered by question ID
using the form's own choice IDs. The IDs here are copied from Finance's form, so they need updating
if Finance edits it.
"""

from django.utils.formats import date_format
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _

from commcare_connect.opportunity.models import (
    InvoiceFinanceRequest,
    InvoiceFinanceRequestStatus,
    InvoiceStatus,
    Payment,
)
from commcare_connect.opportunity.utils.invoice_line_items import get_invoice_service_summary, invoice_period_label

SERVICE_DESK_ID = "19"
REQUEST_TYPE_ID = "186"


class Question:
    PRIORITY = "2"
    APPROVED = "19"
    APPROVER_NAME = "21"
    CONTRACTED_ENTITY = "63"
    STATEMENT_PERIOD = "71"
    NEW_VENDOR = "64"
    INVOICE_UPLOAD = "68"
    GL_ACCOUNT = "27"
    PROJECT_NAME = "29"
    SUBDIVISION = "36"
    MULTIPLE_ACCOUNTS = "37"
    BRIEF_DESCRIPTION = "45"
    ADDITIONAL_INFORMATION = "59"


PRIORITY_STANDARD = "3"
APPROVED_WITH_INTERNAL_APPROVAL = "1"
NEW_VENDOR_YES = "1"
NEW_VENDOR_NO = "2"
SUBDIVISION_CONNECT_DELIVERY = "50"
SINGLE_GL_ACCOUNT_AND_SUBDIVISION = "2"

CONTRACTED_ENTITY_CHOICES = [
    ("1", _("INC - Dimagi, Inc.")),
    ("2", _("DSA - Dimagi South Africa")),
    ("3", _("DSI - Dimagi Software Innovations India")),
]

# Direct accounts are for project expenses and need the Salesforce project name.
DIRECT_GL_ACCOUNTS = [
    ("1", "Direct Communications"),
    ("2", "Direct Consulting"),
    ("3", "Direct Project Equipment"),
    ("66", "Direct Sub-Contracts"),
    ("4", "Direct Translations Services"),
    ("5", "Direct Travel Air & Rail"),
    ("6", "Direct Travel Lodging"),
    ("7", "Direct Travel Meals"),
    ("8", "Direct Travel Other"),
    ("9", "Direct Travel Per Diem"),
    ("10", "Direct Travel Transportation"),
    ("26", "Other Direct Costs"),
]

# Overhead accounts are for costs Dimagi bears. Finance's form also lists Fringe, G&A, hosting and
# payroll accounts; none of those apply to paying an organization for Connect delivery.
OVERHEAD_GL_ACCOUNTS = [
    ("27", "Overhead - Professional Development - Individual"),
    ("95", "Overhead - COIDA Fees"),
    ("28", "Overhead - Computer Equipment"),
    ("29", "Overhead - Conference Sponsorships"),
    ("30", "Overhead - Consulting & Subcontracts"),
    ("65", "Overhead - Engagement and Events"),
    ("31", "Overhead - Home Office Expense"),
    ("81", "Overhead - IT Management"),
    ("32", "Overhead - Mobile Phones"),
    ("33", "Overhead - Office Books & Education"),
    ("34", "Overhead - Office Cleaning"),
    ("35", "Overhead - Office Meals"),
    ("36", "Overhead - Office Meals - Coffee Challenge"),
    ("37", "Overhead - Office Other"),
    ("38", "Overhead - Office Postage & Delivery"),
    ("39", "Overhead - Office Recycling"),
    ("40", "Overhead - Office Repair & Maintenance"),
    ("41", "Overhead - Office Security"),
    ("42", "Overhead - Office Stationery & Printing"),
    ("43", "Overhead - Office Supplies"),
    ("44", "Overhead - Office Utilities"),
    ("45", "Overhead - Online Services"),
    ("46", "Overhead - Professional Development - Team"),
    ("47", "Overhead - Recruiting Agencies"),
    ("48", "Overhead - Recruiting Sources"),
    ("49", "Overhead - Remote Working Expenses"),
    ("96", "Overhead - SETA Training"),
    ("50", "Overhead - Software"),
    ("51", "Overhead - Summit"),
    ("52", "Overhead - Team Events"),
    ("53", "Overhead - Translation Services"),
    ("54", "Overhead - Travel Air & Rail"),
    ("55", "Overhead - Travel Conferences"),
    ("56", "Overhead - Travel Lodging"),
    ("57", "Overhead - Travel Meals"),
    ("58", "Overhead - Travel Meals with Client"),
    ("59", "Overhead - Travel Other"),
    ("60", "Overhead - Travel Transportation"),
    ("61", "Overhead - Travel Visa Fees"),
    ("62", "Overhead - Voice & Data"),
    ("63", "Overhead - Well Being"),
]

GL_ACCOUNT_CHOICES = [(_("Direct"), DIRECT_GL_ACCOUNTS), (_("Overhead"), OVERHEAD_GL_ACCOUNTS)]


def requires_project_name(gl_account: str) -> bool:
    return gl_account in dict(DIRECT_GL_ACCOUNTS)


def can_submit_finance_request(invoice) -> bool:
    """Only approved invoices go to Finance, once each; a failed submission can be sent again."""
    if invoice.status != InvoiceStatus.READY_TO_PAY:
        return False
    existing = getattr(invoice, "finance_request", None)
    return existing is None or existing.status == InvoiceFinanceRequestStatus.FAILED


def get_previous_finance_request(opportunity):
    """The opportunity's most recent request, whose answers prefill the next one."""
    return InvoiceFinanceRequest.objects.filter(invoice__opportunity=opportunity).order_by("-date_created").first()


def suggest_new_vendor(organization) -> bool:
    """Whether Dimagi would be paying this organization for the first time, as far as Connect knows.

    Payments made outside Connect are invisible here, so this is only a suggestion for the PM.
    """
    paid_before = Payment.objects.filter(invoice__opportunity__organization=organization).exists()
    requested_before = InvoiceFinanceRequest.objects.filter(
        invoice__opportunity__organization=organization, status=InvoiceFinanceRequestStatus.SUBMITTED
    ).exists()
    return not (paid_before or requested_before)


def build_request_fields(finance_request) -> dict:
    invoice = finance_request.invoice
    summary = gettext("Connect Payment request: %(organization)s %(period)s") % {
        "organization": invoice.opportunity.organization.name,
        "period": invoice_period_label(invoice),
    }
    return {"summary": summary}


def build_form_answers(finance_request, invoice_attachment_id: str) -> dict:
    invoice = finance_request.invoice
    answers = {
        Question.PRIORITY: {"choices": [PRIORITY_STANDARD]},
        Question.APPROVED: {"choices": [APPROVED_WITH_INTERNAL_APPROVAL]},
        Question.APPROVER_NAME: {"text": _approver(finance_request)["name"]},
        Question.CONTRACTED_ENTITY: {"choices": [finance_request.contracted_entity]},
        Question.STATEMENT_PERIOD: {"text": invoice_period_label(invoice)},
        Question.NEW_VENDOR: {"choices": [NEW_VENDOR_YES if finance_request.new_vendor else NEW_VENDOR_NO]},
        Question.INVOICE_UPLOAD: {"files": [invoice_attachment_id]},
        Question.GL_ACCOUNT: {"choices": [finance_request.gl_account]},
        Question.SUBDIVISION: {"choices": [SUBDIVISION_CONNECT_DELIVERY]},
        Question.MULTIPLE_ACCOUNTS: {"choices": [SINGLE_GL_ACCOUNT_AND_SUBDIVISION]},
        Question.BRIEF_DESCRIPTION: {"text": _brief_description(invoice)},
        Question.ADDITIONAL_INFORMATION: {"adf": _to_adf(get_additional_information_lines(finance_request))},
    }
    # The form only shows, and only accepts, a project name for Direct accounts.
    if requires_project_name(finance_request.gl_account):
        answers[Question.PROJECT_NAME] = {"text": finance_request.project_name}
    return answers


def get_additional_information_lines(finance_request) -> list[str]:
    """The request's full description, one entry per line, in the shape Finance asked for."""
    invoice = finance_request.invoice
    opportunity = invoice.opportunity
    currency = opportunity.currency_code
    approver = _approver(finance_request)

    lines = [
        gettext("Vendor: %(name)s") % {"name": opportunity.organization.name},
        gettext("Connect Opportunity: %(name)s") % {"name": opportunity.name},
        gettext("Invoice: %(number)s") % {"number": invoice.invoice_number},
        gettext("Description of services:"),
    ]
    lines += [
        f"- {line.label}: {currency} {_money(line.amount_local)}" for line in get_invoice_service_summary(invoice)
    ]
    total = gettext("Total: %(currency)s %(amount)s") % {"currency": currency, "amount": _money(invoice.amount)}
    if invoice.amount_usd is not None:
        total += f" (USD {_money(invoice.amount_usd)})"
    lines.append(total)
    if approver["date"]:
        lines.append(gettext("Approved by %(name)s on %(date)s") % approver)
    else:
        lines.append(gettext("Approved by %(name)s") % approver)
    lines.append(
        gettext("Submitted for CommCare Connect by %(user)s") % {"user": _user_label(finance_request.submitted_by)}
    )
    return lines


def _approver(finance_request) -> dict:
    """The PM who approved the invoice for payment, falling back to the person submitting it.

    Invoices approved before approvals were recorded have no certification to read.
    """
    certification = finance_request.invoice.pm_certification
    if certification:
        return {"name": certification["name"], "date": date_format(certification["certified_at"], "DATE_FORMAT")}
    return {"name": _user_label(finance_request.submitted_by), "date": None}


def _brief_description(invoice) -> str:
    return gettext("Connect payment to %(organization)s for %(opportunity)s, invoice %(number)s") % {
        "organization": invoice.opportunity.organization.name,
        "opportunity": invoice.opportunity.name,
        "number": invoice.invoice_number,
    }


def _user_label(user) -> str:
    if user is None:
        return gettext("Unknown user")
    return f"{user.name} ({user.email})" if user.name else user.email


def _money(amount) -> str:
    return f"{amount:,.2f}"


def _to_adf(lines: list[str]) -> dict:
    """Atlassian Document Format for a rich text answer, one paragraph per line."""
    return {
        "version": 1,
        "type": "doc",
        "content": [{"type": "paragraph", "content": [{"type": "text", "text": line}]} for line in lines if line],
    }
