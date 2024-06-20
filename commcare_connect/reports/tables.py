from django_tables2 import columns, tables


class AdminReportTable(tables.Table):
    quarter = columns.Column(verbose_name="Quarter")
    users = columns.Column(verbose_name="Active Users")
    services = columns.Column(verbose_name="Verified Services")
    payments = columns.Column(verbose_name="Amount Paid")
    beneficiaries = columns.Column(verbose_name="Beneficiaries Served")

    class Meta:
        empty_text = "No data for this quarter."
        orderable = False
