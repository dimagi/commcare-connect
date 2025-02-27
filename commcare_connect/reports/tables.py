import calendar

from django.urls import reverse
from django.utils.html import format_html
from django_tables2 import columns, tables

from commcare_connect.opportunity.tables import SumColumn


class AdminReportTable(tables.Table):
    month = columns.Column(verbose_name="Month", footer="Total", empty_values=())
    delivery_type_name = columns.Column(verbose_name="Delivery Type", empty_values=())
    users = SumColumn(verbose_name="Eligible Users")
    avg_time_to_payment = columns.Column(verbose_name="Average Time to Payment")
    max_time_to_payment = columns.Column(verbose_name="Max Time to Payment")
    flw_amount_earned = SumColumn(verbose_name="FLW Amount Earned")
    flw_amount_paid = SumColumn(verbose_name="FLW Amount Paid")
    nm_amount_earned = SumColumn(verbose_name="NM Amount Earned")
    nm_amount_paid = SumColumn(verbose_name="NM Amount Paid")
    nm_other_amount_paid = SumColumn(verbose_name="NM Other Amount Paid")
    services = SumColumn(verbose_name="Verified Services")
    avg_top_paid_flws = SumColumn(verbose_name="Average paid to Top FLWs")

    class Meta:
        empty_text = "No data for this month."
        orderable = False
        row_attrs = {"id": lambda record: f"row{record['month_group'].year}-{record['month_group'].month:02}"}

    def render_month(self, record):
        date = record["month_group"]
        return f"{calendar.month_name[date.month]} {date.year}"

    def render_delivery_type_name(self, record, value):
        if value is not None and value != "All":
            return value
        url = reverse("reports:delivery_stats_report")
        filter_date = record["month_group"].strftime("%Y-%m")
        return format_html(
            """<button type="button" class="btn btn-primary btn-sm"
                 hx-get='{url}?from_date={filter_date}&to_date={filter_date}&group_by_delivery_type=on&drilldown'
                 hx-target='#row{filter_date}'
                 hx-swap="outerHTML"
                 hx-select="tbody tr">
                 View all types
               </button>""",
            url=url,
            filter_date=filter_date,
        )

    def render_avg_time_to_payment(self, record, value):
        return f"{value} days"

    def render_max_time_to_payment(self, record, value):
        return f"{value} days"
