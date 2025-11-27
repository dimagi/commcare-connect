from django_tables2 import columns, tables

from commcare_connect.opportunity.tables import SumColumn


class AdminReportTable(tables.Table):
    month = columns.Column(verbose_name="Month", footer="Total", empty_values=())
    delivery_type_name = columns.Column(verbose_name="Delivery Type", empty_values=("All"))
    connectid_users = columns.Column(verbose_name="ConnectID Accounts")
    non_preregistered_users = columns.Column(verbose_name="Non-Preregistered Users")
    total_eligible_users = columns.Column(verbose_name="Total Paid Users")
    users = SumColumn(verbose_name="Paid Users")
    activated_peronsalid_accounts = columns.Column(verbose_name="Activated PersonalID Accounts")
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
        row_attrs = {"id": lambda record: f"row{record['month_group'].strftime('%Y-%m')}"}

    def render_month(self, record):
        return record["month_group"].strftime("%B %Y")

    def render_avg_time_to_payment(self, record, value):
        return f"{value:.2f} days"

    def render_max_time_to_payment(self, record, value):
        return f"{value} days"
