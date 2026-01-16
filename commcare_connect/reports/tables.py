from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import gettext as _
from django_tables2 import columns, tables

from commcare_connect.opportunity.models import PaymentInvoice
from commcare_connect.opportunity.tables import SumColumn
from commcare_connect.utils.tables import DMYTColumn


class AdminReportTable(tables.Table):
    month = columns.Column(verbose_name="Month", footer="Total", empty_values=())
    delivery_type_name = columns.Column(verbose_name="Delivery Type", empty_values=("All"))
    connectid_users = columns.Column(verbose_name="ConnectID Accounts")
    non_preregistered_users = columns.Column(verbose_name="Non-Preregistered Users")
    activated_connect_users = columns.Column(verbose_name="Activated Connect Users")
    users = SumColumn(verbose_name="Monthly Activated Connect Users")
    hq_sso_users = columns.Column("PersonalID users authenticated to CCHQ app")
    activated_personalid_accounts = columns.Column(verbose_name="Activated PersonalID Accounts")
    avg_time_to_payment = columns.Column(verbose_name="Average Time to Payment")
    max_time_to_payment = columns.Column(verbose_name="Max Time to Payment")
    flw_amount_earned = SumColumn(verbose_name="FLW Amount Earned")
    flw_amount_paid = SumColumn(verbose_name="FLW Amount Paid")
    intervention_funding_deployed = SumColumn(verbose_name="Intervention Funding Deployed")
    organization_funding_deployed = SumColumn(verbose_name="Organization Funding Deployed")
    services = SumColumn(verbose_name="Verified Services")
    avg_top_earned_flws = SumColumn(verbose_name="Average Earned by Top FLWs")

    class Meta:
        empty_text = "No data for this month."
        orderable = False
        row_attrs = {"id": lambda record: f"row{record['month_group'].strftime('%Y-%m')}"}

    def render_month(self, record):
        return record["month_group"].strftime("%B %Y")

    def render_avg_time_to_payment(self, record, value):
        return f"{value:.2f} days"

    def render_max_time_to_payment(self, record, value):
        return f"{value} days"


class InvoiceReportTable(tables.Table):
    opportunity_id = columns.Column(
        accessor="opportunity__id",
        verbose_name=_("Opportunity ID"),
    )
    opportunity_name = columns.Column(
        accessor="opportunity__name",
        verbose_name=_("Opportunity Name"),
    )
    invoice_number = columns.Column(orderable=False, verbose_name=_("Invoice Number"))
    amount = columns.Column(verbose_name=_("Amount"))
    amount_usd = columns.Column(verbose_name=_("Amount (USD)"))
    invoice_type = columns.Column(verbose_name=_("Type"), accessor="service_delivery")
    status = columns.Column(verbose_name=_("Status"))
    date = DMYTColumn(verbose_name=_("Date of Payment"), accessor="date_paid")

    class Meta:
        model = PaymentInvoice
        fields = (
            "opportunity_id",
            "opportunity_name",
            "invoice_number",
            "amount",
            "amount_usd",
            "invoice_type",
            "status",
            "date",
        )

    def render_invoice_number(self, value, record):
        url = reverse(
            "opportunity:invoice_review",
            args=[record.org_slug, record.opportunity_id, record.id],
        )
        return format_html(
            '<a href="{}" class="underline text-brand-deep-purple">{}</a>',
            url,
            value,
        )

    def value_invoice_number(self, value, record):
        return value

    def render_amount(self, record):
        return f"{record.opportunity.currency_code} {record.amount}"

    def render_invoice_type(self, record):
        return (
            PaymentInvoice.InvoiceType.service_delivery.label
            if record.service_delivery
            else PaymentInvoice.InvoiceType.custom.label
        )
