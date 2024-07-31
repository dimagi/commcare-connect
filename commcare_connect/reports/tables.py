from django.utils.safestring import mark_safe
from django_tables2 import columns, tables


class AdminReportTable(tables.Table):
    quarter = columns.Column(verbose_name="Quarter")
    users = columns.Column(verbose_name="Active Users")
    services = columns.Column(verbose_name="Verified Services")
    approved_payments = columns.Column(verbose_name="Acknowledged Payments")
    total_payments = columns.Column(verbose_name="Total Payments")
    beneficiaries = columns.Column(verbose_name="Beneficiaries Served")

    class Meta:
        empty_text = "No data for this quarter."
        orderable = False

    def render_total_payments(self, value):
        return mark_safe("<br>".join(value))

    def render_approved_payments(self, value):
        return mark_safe("<br>".join(value))
