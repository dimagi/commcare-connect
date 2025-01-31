from django.urls import reverse
from django.utils.html import format_html
from django_tables2 import columns, tables


class AdminReportTable(tables.Table):
    month = columns.Column(verbose_name="Month")
    delivery_type = columns.Column(verbose_name="Delivery Type")
    users = columns.Column(verbose_name="Active Users")
    services = columns.Column(verbose_name="Verified Services")
    approved_payments = columns.Column(verbose_name="Acknowledged Payments")
    total_payments = columns.Column(verbose_name="Total Payments")
    beneficiaries = columns.Column(verbose_name="Beneficiaries Served")

    class Meta:
        empty_text = "No data for this month."
        orderable = False
        row_attrs = {"id": lambda record: f"row{record['month'][0]}-{record['month'][1]}"}

    def render_month(self, value):
        return f"{value[0]} Q{value[1]}"

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
            year=record["month"][0],
            month=record["month"][1],
        )
