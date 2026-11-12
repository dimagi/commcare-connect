"""Tables for the invoice list, invoice line items and the line-item CSV export."""

import django_tables2 as tables
from dateutil.relativedelta import relativedelta
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.safestring import mark_safe
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django_tables2 import columns

from commcare_connect.opportunity.models import InvoiceStatus, PaymentInvoice
from commcare_connect.opportunity.tables import OpportunityContextTable, header_with_tooltip, value_with_icon_tooltip
from commcare_connect.opportunity.utils.invoice import with_invoice_month
from commcare_connect.utils.datetime import get_month_start_date
from commcare_connect.utils.tables import DMYTColumn, select_column


class PaymentInvoiceTable(OpportunityContextTable):
    select = select_column(
        td_extra={
            "@change": "updateSelectAll()",
            # Read by the selection bar to total up what the export will cover. Empty rather than
            # zero when an invoice has no USD amount, so the bar can say so instead of quietly
            # understating the total.
            "data-amount-usd": lambda record: "" if record.amount_usd is None else record.amount_usd,
        }
    )
    amount = tables.Column(verbose_name="Amount")
    payment_date = columns.Column(verbose_name="Payment Date", accessor="payment", empty_values=(None))
    actions = tables.Column(empty_values=(), orderable=False, verbose_name="Actions")
    exchange_rate = tables.Column(orderable=False, empty_values=(None,), accessor="exchange_rate__rate")
    amount_usd = tables.Column(
        verbose_name=header_with_tooltip(
            "Amount (USD)",
            "Sum of USD amount over all line items in this invoice. Each line item is calculated as "
            "approved count × (payment unit amount ÷ exchange rate at time of approval) rounded to 2 "
            "decimals for each delivery",
        ),
    )
    status = tables.Column(verbose_name="Invoice Status")
    invoice_type = tables.Column(verbose_name="Invoice Type", accessor="service_delivery", empty_values=())
    date = tables.Column(verbose_name=gettext_lazy("Invoice Generation Date"))
    last_status_modified_at = tables.Column(
        verbose_name=gettext_lazy("Invoice Last Updated Date"), accessor="last_status_modified_at"
    )

    class Meta:
        model = PaymentInvoice
        orderable = False
        fields = ("amount", "date", "invoice_number")
        sequence = (
            "select",
            "amount",
            "amount_usd",
            "exchange_rate",
            "date",
            "invoice_number",
            "status",
            "last_status_modified_at",
            "payment_date",
            "invoice_type",
            "actions",
        )
        empty_text = "No Payment Invoices"
        row_attrs = {
            "class": lambda record, table: (
                "bg-yellow-100" if record.invoice_number == table.highlight_invoice_number else ""
            ),
        }

    def __init__(self, *args, **kwargs):
        self.csrf_token = kwargs.pop("csrf_token")
        self.opportunity = kwargs.pop("opportunity")
        self.highlight_invoice_number = kwargs.pop("highlight_invoice_number", None)
        self.is_pm = kwargs.pop("is_pm", False)
        self.month_param = kwargs.pop("month_param", None)
        super().__init__(*args, **kwargs)
        self.base_columns["amount"].verbose_name = f"Amount ({self.opportunity.currency_code})"

    def render_exchange_rate(self, value):
        return f"{round(value, 2)} {self.opportunity.currency_code} per USD"

    def render_payment_date(self, value):
        if value is not None:
            return value.date_paid
        return

    def render_invoice_type(self, record):
        if record.service_delivery:
            return _("Service Delivery")
        return _("Other")

    def render_status(self, record):
        return record.get_status_display()

    def render_actions(self, record):
        invoice_review_url = with_invoice_month(
            reverse(
                "opportunity:invoice_review",
                args=[self.org_slug, str(self.opportunity.opportunity_id), str(record.payment_invoice_id)],
            ),
            self.month_param,
        )
        review_button = (
            f'<a href="{invoice_review_url}" '
            f'class="button button-md outline-style !inline-flex justify-center">'
            f"{_('Review')}</a>"
        )
        pay_button = ""
        if self.is_pm:
            if record.status == InvoiceStatus.READY_TO_PAY:
                invoice_pay_url = with_invoice_month(
                    reverse("opportunity:invoice_pay", args=[self.org_slug, self.opportunity.opportunity_id]),
                    self.month_param,
                )
                disabled = "disabled" if getattr(record, "payment", None) else ""
                pay_button = f"""
                    <button
                        hx-post="{invoice_pay_url}"
                        hx-vals='{{"pk": "{record.payment_invoice_id}"}}'
                        hx-headers='{{"X-CSRFToken": "{self.csrf_token}"}}'
                        class="button button-md primary-dark"
                        {disabled}>
                        {_("Pay")}
                    </button>
                """
        return mark_safe(f'<div class="flex gap-2">{review_button}{pay_button}</div>')


class InvoiceLineItemsTable(tables.Table):
    month = tables.Column(verbose_name=gettext_lazy("Month"))
    payment_unit_name = tables.Column(verbose_name=gettext_lazy("Payment Unit"))
    number_approved = tables.Column(verbose_name=gettext_lazy("Number Approved"))
    flw_amount_local = tables.Column(accessor="flw_pay__local", verbose_name=gettext_lazy("FLW Pay (local)"))
    org_amount_local = tables.Column(accessor="org_pay__local", verbose_name=gettext_lazy("Org Pay (local)"))
    total_amount_local = tables.Column(accessor="total_pay__local", verbose_name=gettext_lazy("Total Pay (local)"))
    exchange_rate = tables.Column(verbose_name=gettext_lazy("Exchange Rate"))
    total_amount_usd = tables.Column(accessor="total_pay__usd", verbose_name=gettext_lazy("Total Pay (USD)"))

    def __init__(self, currency, *args, show_org=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.currency = currency
        if currency:
            self.columns["flw_amount_local"].column.verbose_name = _("FLW Pay (%(currency)s)") % {"currency": currency}
            self.columns["org_amount_local"].column.verbose_name = _("Org Pay (%(currency)s)") % {"currency": currency}
            self.columns["total_amount_local"].column.verbose_name = _("Total Pay (%(currency)s)") % {
                "currency": currency
            }
        if show_org:
            usd_tooltip = _(
                "Sum of FLW pay and org pay (USD), each converted at the exchange rate "
                "at the delivery's approval time and rounded to 2 decimals per delivery."
            )
        else:
            self.columns["flw_amount_local"].column.visible = False
            self.columns["org_amount_local"].column.visible = False
            usd_tooltip = _(
                "FLW pay (USD), converted at the exchange rate at the delivery's approval time "
                "and rounded to 2 decimals per delivery."
            )
        self.columns["total_amount_usd"].column.verbose_name = header_with_tooltip(_("Total Pay (USD)"), usd_tooltip)

    class Meta:
        orderable = False
        empty_text = gettext_lazy("No invoice items found.")
        attrs = {"class": "min-w-full rounded-lg shadow-md bg-white", "thead": {"class": "bg-gray-100"}}
        row_attrs = {"class": "even:bg-gray-50 text-gray-800 hover:bg-gray-100"}

    def render_month(self, value):
        return value.strftime("%B %Y")

    def render_exchange_rate(self, value, record):
        EXCHANGE_RATE_LEARN_MORE_URL = (
            "https://dimagi.atlassian.net/wiki/spaces/connectpublic/pages/3214934056/Managing+Currencies+in+Connect"
        )
        rate_date = record.exchange_rate_date
        next_update_date = get_month_start_date(rate_date) + relativedelta(months=1) if rate_date else None
        if next_update_date and next_update_date <= timezone.localdate():
            # A stale fallback rate (looked up from a much older month) shouldn't claim an
            # update date that's already passed -- we don't know when it'll actually refresh.
            next_update_date = None
        tooltip_html = render_to_string(
            "opportunity/partials/exchange_rate_tooltip.html",
            {
                "rate": value,
                "currency": self.currency,
                "rate_date": rate_date,
                "next_update_date": next_update_date,
                "fetched_at": record.exchange_rate_fetched_at,
                "learn_more_url": EXCHANGE_RATE_LEARN_MORE_URL,
            },
        )
        return value_with_icon_tooltip(str(value), tooltip_html, theme="dark")


class InvoiceDeliveriesTable(tables.Table):
    payment_unit = tables.Column(
        accessor="completed_work__payment_unit__name", verbose_name=gettext_lazy("Payment Unit")
    )
    opportunity = tables.Column(
        accessor="completed_work__payment_unit__opportunity__name", verbose_name=gettext_lazy("Opportunity")
    )
    entity_name = tables.Column(accessor="completed_work__entity_name", verbose_name=gettext_lazy("Beneficiary"))
    username = tables.Column(
        accessor="completed_work__opportunity_access__user__name", verbose_name=gettext_lazy("Worker")
    )
    first_visit_date = DMYTColumn(
        accessor="completed_work__date_created", verbose_name=gettext_lazy("First Visit Date")
    )
    first_approved_date = DMYTColumn(
        accessor="completed_work__status_modified_date", verbose_name=gettext_lazy("First Approved Date")
    )
    billing_month = tables.Column(accessor="month", verbose_name=gettext_lazy("Billing Month"))
    billing_type = tables.Column(accessor="is_delta", verbose_name=gettext_lazy("Billing Type"))
    approved_count = tables.Column(accessor="billed_count", verbose_name=gettext_lazy("Approved Deliveries"))
    flw_amount_local = tables.Column(accessor="flw_pay__local", verbose_name=gettext_lazy("FLW Pay"))
    org_amount_local = tables.Column(accessor="org_pay__local", verbose_name=gettext_lazy("Org Pay"))
    total_amount_local = tables.Column(accessor="total_pay__local", verbose_name=gettext_lazy("Total Pay"))
    total_amount_usd = tables.Column(accessor="total_pay__usd", verbose_name=gettext_lazy("Total Pay (USD)"))

    def __init__(self, currency, *args, show_org=False, **kwargs):
        super().__init__(*args, **kwargs)
        if currency:
            self.columns["flw_amount_local"].column.verbose_name = _("FLW Pay (%(currency)s)") % {"currency": currency}
            self.columns["org_amount_local"].column.verbose_name = _("Org Pay (%(currency)s)") % {"currency": currency}
            self.columns["total_amount_local"].column.verbose_name = _("Total Pay (%(currency)s)") % {
                "currency": currency
            }
        if not show_org:
            self.columns["flw_amount_local"].column.exclude_from_export = True
            self.columns["org_amount_local"].column.exclude_from_export = True

    class Meta:
        orderable = False
        sequence = (
            "payment_unit",
            "opportunity",
            "entity_name",
            "username",
            "first_visit_date",
            "first_approved_date",
            "billing_month",
            "billing_type",
            "approved_count",
            "flw_amount_local",
            "org_amount_local",
            "total_amount_local",
            "total_amount_usd",
        )

    def render_billing_month(self, value):
        return value.strftime("%B %Y")

    def render_billing_type(self, value):
        return _("Additional Delivery") if value else _("First Billing")
