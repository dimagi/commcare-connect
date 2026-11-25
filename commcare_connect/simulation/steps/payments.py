import datetime
import json
import re
from decimal import ROUND_DOWN, Decimal
from html import unescape

from django.urls import reverse
from django.utils.timezone import localdate
from django.utils.translation import gettext

from commcare_connect.opportunity.models import InvoiceStatus, OpportunityAccess, Payment, PaymentInvoice
from commcare_connect.opportunity.utils.invoice import InvoiceWorkflow
from commcare_connect.opportunity.utils.invoice_line_items import get_billable_completed_works_qs
from commcare_connect.opportunity.visit_import import PAYMENT_COLS, bulk_update_payments
from commcare_connect.simulation.steps.browser import StepRequestError

CENTS = Decimal("0.01")

# (payment method, payment operator) pairs a network manager might record in the payment import.
PAYMENT_CHANNELS = (
    ("mobile money", "M-Pesa"),
    ("mobile money", "MTN MoMo"),
    ("mobile money", "Airtel Money"),
    ("bank transfer", "Equity Bank"),
)

# What the network manager writes in the invoice's notes box when submitting or cancelling it. The program
# manager's page shows the notes read-only, so its changes send them back unchanged.
NM_STATUS_NOTES = {
    InvoiceStatus.PENDING_PM_REVIEW: "Deliveries checked against the visit log.",
    InvoiceStatus.CANCELLED_BY_NM: "Raised in error; will reissue.",
}
SERVICE_DELIVERY = PaymentInvoice.InvoiceType.service_delivery
# What the invoice pay view tells the user when it has paid something; it says nothing when it has not.
PAID_MESSAGE = gettext("Invoice(s) successfully marked as paid.")
INPUT_TAG = re.compile(r"<input\b[^>]*>")


class PaymentSteps:
    """Paying workers and invoicing the program manager."""

    def pay(self, opportunity, workers=None, *, fraction=1.0, payment_date=None):
        """Pay each worker ``fraction`` of what they have accrued, through the payment import.

        ``workers`` defaults to everyone with an accrued balance. Returns the created ``Payment``s.
        """
        with self.step():
            accesses = OpportunityAccess.objects.filter(opportunity=opportunity).select_related("user")
            if workers is not None:
                accesses = accesses.filter(user__in=workers)
            paid_on = (payment_date or localdate()).isoformat()
            rows = [
                [access.user.username, str(amount), paid_on, *self.rng.choice(PAYMENT_CHANNELS)]
                for access in accesses.order_by("user__username")
                if (amount := _share_of_unpaid(access, fraction)) > 0
            ]
            if not rows:
                self.log(f"Nothing to pay on {opportunity.name}")
                return []

            opportunity_payments = Payment.objects.filter(opportunity_access__opportunity=opportunity)
            existing = set(opportunity_payments.values_list("pk", flat=True))
            bulk_update_payments(opportunity.id, PAYMENT_COLS, rows)
            payments = list(opportunity_payments.exclude(pk__in=existing))

        self.log(f"Paid {len(payments)} workers on {opportunity.name}")
        self.link(
            f"{opportunity.name} payments",
            reverse("opportunity:worker_payments", args=(opportunity.organization.slug, opportunity.opportunity_id)),
        )
        return payments

    def invoice(self, opportunity, *, end_date=None):
        """Bill approved work not yet invoiced, up to ``end_date``. Returns the ``PaymentInvoice``.

        Fills in the network manager's "New Service Delivery Invoice" page as ``nm_admin``: the window starts where
        the page suggests and ends on ``end_date``, and the total is the one the page previews for that window.
        ``end_date`` defaults to yesterday, the latest end the page accepts, so work approved today waits for
        tomorrow's invoice. Returns None when there is nothing to bill.
        """
        end_date = end_date or localdate() - datetime.timedelta(days=1)
        browser = self.browser(self.nm_admin)
        create_url = reverse("opportunity:invoice_create", args=_nm_url_args(opportunity))
        with self.step():
            initial = _input_values(browser.get(create_url, invoice_type=SERVICE_DELIVERY))
            start_date = datetime.date.fromisoformat(initial["start_date"])
            # The page previews its suggested window as it loads, and again once the end date is changed.
            preview = _preview_invoice(browser, opportunity, start_date, initial["end_date"])
            if initial["end_date"] != end_date.isoformat():
                preview = _preview_invoice(browser, opportunity, start_date, end_date.isoformat())
            billable = (
                start_date <= end_date and get_billable_completed_works_qs(opportunity, start_date, end_date).exists()
            )
            if not billable:
                self.log(f"Nothing to invoice on {opportunity.name}")
                return None

            browser.post(
                f"{create_url}?invoice_type={SERVICE_DELIVERY}",
                {
                    "invoice_number": initial["invoice_number"],
                    "title": "",
                    "date": initial["date"],
                    "start_date": start_date.isoformat(),
                    "end_date": end_date.isoformat(),
                    "amount": preview["total_amount"],
                    "amount_usd": preview["total_usd_amount"],
                    "description": "",
                    "submit": "Submit",
                },
            )
            invoice = PaymentInvoice.objects.get(opportunity=opportunity, invoice_number=initial["invoice_number"])

        self.log(f"Invoiced {invoice.amount} {opportunity.currency_code} on {opportunity.name}")
        self.link(f"{opportunity.name} invoice {invoice.invoice_number}", _invoice_review_url(invoice))
        return invoice

    def invoice_status(self, invoice, status):
        """Move ``invoice`` to ``status`` as the role allowed to make that transition.

        Presses the invoice page's button for it, as ``pm_admin`` from the program manager's organization or as
        ``nm_admin`` from the network manager's; submitting to the program manager is certified as the page asks.
        """
        # The page shows the invoice as it is now, and the program manager's buttons send its notes back unchanged.
        invoice.refresh_from_db()
        opportunity = invoice.opportunity
        if InvoiceWorkflow.can_role_perform_action("program_manager", status):
            user, url_args, notes = self.pm_admin, _pm_url_args(opportunity), invoice.description
        else:
            user, url_args, notes = self.nm_admin, _nm_url_args(opportunity), NM_STATUS_NOTES.get(status)
        data = {"invoice_id": invoice.payment_invoice_id, "description": notes or "", "new_status": status}
        if status == InvoiceStatus.PENDING_PM_REVIEW:
            data["attestation"] = "true"
        with self.step():
            # The view answers a change it made with 204 and a refusal with 400. A user who passes its access check
            # without belonging to the organization (all-org access) gets a 302 redirect instead.
            self.browser(user).post(
                reverse("opportunity:invoice_update_status", args=url_args),
                data,
                htmx=True,
                succeeded=lambda response: response.status_code == 204,
            )
        invoice.refresh_from_db()
        self.log(f"Invoice {invoice.invoice_number}: {InvoiceStatus.get_label(status)}")
        return invoice

    def invoice_pay(self, invoice):
        """Mark ``invoice`` paid, as ``pm_admin``, with the invoice page's Pay button."""
        with self.step():
            # The view answers with 200 and HX-Redirect whether or not it paid anything (an invoice already paid is
            # skipped), and says so only when it did. A refusal is a 400; a user who passes its access check without
            # being the opportunity's program manager gets a 302 redirect.
            self.browser(self.pm_admin).post(
                reverse("opportunity:invoice_pay", args=_pm_url_args(invoice.opportunity)),
                {"pk": invoice.payment_invoice_id},
                htmx=True,
                succeeded=lambda response: response.status_code == 200 and PAID_MESSAGE in response.step_messages,
            )
        invoice.refresh_from_db()
        self.log(f"Invoice {invoice.invoice_number} paid")
        return invoice


def _share_of_unpaid(access, fraction):
    """``fraction`` of what ``access`` has accrued and not been paid, rounded down to the cent."""
    unpaid = Decimal(access.payment_accrued) - access.total_paid
    return (Decimal(str(fraction)) * unpaid).quantize(CENTS, rounding=ROUND_DOWN)


def _preview_invoice(browser, opportunity, start_date, end_date):
    """The line-item totals the invoice page fetches for the window, as the JSON request it sends.

    ``end_date`` is the end date field's text, as the page sends it.
    """
    url = reverse("opportunity:invoice_items", args=_nm_url_args(opportunity))
    body = json.dumps({"start_date": start_date.isoformat(), "end_date": end_date})
    response = browser.client.post(url, body, content_type="application/json")
    if response.status_code != 200 or "error" in response.json():
        raise StepRequestError(f"POST {url} as {browser.user.email}: {response.status_code}. {response.content[:300]}")
    return response.json()


def _input_values(page):
    """The value of each named ``<input>`` on ``page``, as the browser would submit it untouched."""
    values = {}
    for tag in INPUT_TAG.findall(page.content.decode()):
        name, value = re.search(r'\bname="([^"]*)"', tag), re.search(r'\bvalue="([^"]*)"', tag)
        if name:
            values[name.group(1)] = unescape(value.group(1)) if value else ""
    return values


def _nm_url_args(opportunity):
    return (opportunity.organization.slug, opportunity.opportunity_id)


def _pm_url_args(opportunity):
    return (opportunity.program.organization.slug, opportunity.opportunity_id)


def _invoice_review_url(invoice):
    return reverse("opportunity:invoice_review", args=(*_nm_url_args(invoice.opportunity), invoice.payment_invoice_id))
