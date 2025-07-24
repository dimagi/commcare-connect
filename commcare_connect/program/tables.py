import django_tables2 as tables
from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from .models import ManagedOpportunity


class FunnelPerformanceTable(tables.Table):
    organization = tables.Column()
    opportunity = tables.Column(accessor="name", verbose_name="Opportunity")
    start_date = tables.DateColumn()
    workers_invited = tables.Column(verbose_name=_("Workers Invited"))
    workers_passing_assessment = tables.Column(verbose_name=_("Workers Passing Assessment"))
    workers_starting_delivery = tables.Column(verbose_name=_("Workers Starting Delivery"))
    percentage_conversion = tables.Column(verbose_name=_("% Conversion"))
    average_time_to_convert = tables.Column(verbose_name=_("Average Time To convert"))

    class Meta:
        model = ManagedOpportunity
        empty_text = "No data available yet."
        fields = (
            "organization",
            "opportunity",
            "start_date",
            "workers_invited",
            "workers_passing_assessment",
            "workers_starting_delivery",
            "percentage_conversion",
            "average_time_to_convert",
        )
        orderable = False

    def render_opportunity(self, value, record):
        url = reverse(
            "opportunity:detail",
            kwargs={
                "org_slug": record.organization.slug,
                "opp_id": record.id,
            },
        )
        return format_html('<a href="{}">{}</a>', url, value)

    def render_average_time_to_convert(self, record):
        if not record.average_time_to_convert:
            return "---"
        total_seconds = record.average_time_to_convert.total_seconds()
        hours = total_seconds / 3600
        return f"{round(hours, 2)}hr"


class DeliveryPerformanceTable(tables.Table):
    organization = tables.Column()
    opportunity = tables.Column(accessor="name", verbose_name="Opportunity")
    start_date = tables.DateColumn()
    total_workers_starting_delivery = tables.Column(verbose_name=_("Workers Starting Delivery"))
    active_workers = tables.Column(verbose_name=_("Active Workers"))
    deliveries_per_worker = tables.Column(verbose_name=_("Deliveries per Worker"))
    records_flagged_percentage = tables.Column(verbose_name=_("% Records flagged"))

    class Meta:
        model = ManagedOpportunity
        empty_text = "No data available yet."
        fields = (
            "organization",
            "opportunity",
            "start_date",
            "total_workers_starting_delivery",
            "active_workers",
            "deliveries_per_worker",
            "records_flagged_percentage",
        )
        orderable = False

    def render_opportunity(self, value, record):
        url = reverse(
            "opportunity:detail",
            kwargs={
                "org_slug": record.organization.slug,
                "opp_id": record.id,
            },
        )
        return format_html('<a href="{}">{}</a>', url, value)
