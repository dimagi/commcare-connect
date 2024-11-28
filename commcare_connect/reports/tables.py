from django.urls import reverse
from django.utils.html import format_html
from django_tables2 import columns, tables


class AdminReportTable(tables.Table):
    quarter = columns.Column(verbose_name="Quarter")
    delivery_type = columns.Column(verbose_name="Delivery Type")
    users = columns.Column(verbose_name="Active Users")
    services = columns.Column(verbose_name="Verified Services")
    approved_payments = columns.Column(verbose_name="Acknowledged Payments")
    total_payments = columns.Column(verbose_name="Total Payments")
    beneficiaries = columns.Column(verbose_name="Beneficiaries Served")

    class Meta:
        empty_text = "No data for this quarter."
        orderable = False
        row_attrs = {"id": lambda record: f"row{record['quarter'][0]}-{record['quarter'][1]}"}

    def render_quarter(self, value):
        return f"{value[0]} Q{value[1]}"

    def render_delivery_type(self, record):
        if record["delivery_type"] != "All":
            return record["delivery_type"]
        url = reverse("reports:delivery_stats_report")
        return format_html(
            """<button type="button" class="btn btn-primary btn-sm"
                 hx-get='{url}?year={year}&quarter={quarter}&by_delivery_type=on&drilldown'
                 hx-target='#row{year}-{quarter}'
                 hx-swap="outerHTML"
                 hx-select="tbody tr">
                 View all types
               </button>""",
            url=url,
            year=record["quarter"][0],
            quarter=record["quarter"][1],
        )
