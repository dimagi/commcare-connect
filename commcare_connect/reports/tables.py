from django_tables2 import columns, tables

from commcare_connect.opportunity.tables import SumColumn


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
    nm_amount_earned = SumColumn(verbose_name="NM Amount Earned")
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
