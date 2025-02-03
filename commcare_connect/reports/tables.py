import calendar

from django.urls import reverse
from django.utils.html import format_html
from django_tables2 import columns, tables

from commcare_connect.opportunity.tables import SumColumn


class AdminReportTable(tables.Table):
    month = columns.Column(verbose_name="Month", footer="Total")
    delivery_type = columns.Column(verbose_name="Delivery Type")
    users = SumColumn(verbose_name="Active Users")
    services = SumColumn(verbose_name="Verified Services")
    approved_payments = SumColumn(verbose_name="Acknowledged Payments")
    total_payments = SumColumn(verbose_name="Total Payments")
    beneficiaries = SumColumn(verbose_name="Beneficiaries Served")

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
