import calendar

from django.urls import reverse
from django.utils.html import format_html
from django_tables2 import columns, tables

from commcare_connect.opportunity.tables import SumColumn


class AdminReportTable(tables.Table):
    month = columns.Column(verbose_name="Month", footer="Total")
    delivery_type = columns.Column(verbose_name="Delivery Type")
    connectid_users = SumColumn(verbose_name="ConnectID Accounts")
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
        row_attrs = {"id": lambda record: f"row{record['month'][1]}-{record['month'][0]}"}

    def render_month(self, value):
        return f"{calendar.month_name[value[0]]} {value[1]}"

    def render_delivery_type(self, record):
        if record["delivery_type"] != "All":
            return record["delivery_type"]
        url = reverse("reports:delivery_stats_report")
        return format_html(
            """<button type="button" class="btn btn-primary btn-sm"
                 hx-get='{url}?year={year}&month={month}&by_delivery_type=on&drilldown'
                 hx-target='#row{year}-{month}'
                 hx-swap="outerHTML"
                 hx-select="tbody tr">
                 View all types
               </button>""",
            url=url,
            month=record["month"][0],
            year=record["month"][1],
        )

    def render_avg_time_to_payment(self, record, value):
        return f"{value} days"

    def render_max_time_to_payment(self, record, value):
        return f"{value} days"
