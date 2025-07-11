import django_tables2 as tables
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _

from .models import ManagedOpportunity, Program, ProgramApplication, ProgramApplicationStatus

TABLE_TEMPLATE = "django_tables2/bootstrap5.html"
RESPONSIVE_TABLE_AND_LIGHT_HEADER = {
    "class": "table border table-responsive",
    "thead": {"class": "table-light"},
}


class ProgramInvitationTable(tables.Table):
    program = tables.Column(accessor="program__name", verbose_name=_("Program"))
    start_date = tables.DateColumn(accessor="program__start_date", verbose_name=_("Start Date"))
    end_date = tables.DateColumn(accessor="program__end_date", verbose_name=_("End Date"))

    budget = tables.Column(accessor="program__budget", verbose_name=_("Budget"))

    manage = tables.Column(
        verbose_name=_("Manage"),
        orderable=False,
        empty_values=(),
    )

    def render_budget(self, record):
        return f"{record.program.budget} {record.program.currency}"

    def render_manage(self, record):
        if record.status != ProgramApplicationStatus.INVITED:
            return "-"
        org_slug = self.context["request"].org.slug
        program_id = record.program.id
        apply_url = reverse(
            "program:apply_or_decline_application",
            kwargs={
                "org_slug": org_slug,
                "pk": program_id,
                "application_id": record.id,
                "action": "apply",
            },
        )
        decline_url = reverse(
            "program:apply_or_decline_application",
            kwargs={
                "org_slug": org_slug,
                "pk": program_id,
                "application_id": record.id,
                "action": "decline",
            },
        )
        buttons = [
            {
                "post": True,
                "url": apply_url,
                "text": "Apply",
                "color": "primary",
                "icon": "bi bi-check-circle-fill",
            },
            {
                "post": True,
                "url": decline_url,
                "text": "Decline",
                "color": "warning",
                "icon": "bi bi-x-square-fill",
            },
        ]
        return get_manage_buttons_html(buttons, self.context["request"])

    class Meta:
        model = ProgramApplication
        fields = ("program", "start_date", "end_date", "budget", "manage")
        order_by_field = "invite_sort"
        attrs = RESPONSIVE_TABLE_AND_LIGHT_HEADER
        template_name = TABLE_TEMPLATE
        orderable = False


class ProgramApplicationTable(tables.Table):
    organization = tables.Column()
    created_by = tables.Column()
    status = tables.Column()
    date_modified = tables.DateColumn(verbose_name=_("Updated On"), order_by=("date_modified",))
    manage = tables.Column(
        verbose_name=_("Manage"),
        orderable=False,
        empty_values=(),
    )

    def render_manage(self, record):
        if record.status == ProgramApplicationStatus.INVITED:
            return "-"
        accept_url = reverse(
            "program:manage_application",
            kwargs={"org_slug": self.context["request"].org.slug, "application_id": record.id, "action": "accept"},
        )
        reject_url = reverse(
            "program:manage_application",
            kwargs={"org_slug": self.context["request"].org.slug, "application_id": record.id, "action": "reject"},
        )

        buttons = [
            {
                "post": True,
                "url": accept_url,
                "text": _("Accept"),
                "color": "success",
                "icon": "bi bi-check-circle-fill",
                "disable": record.status != ProgramApplicationStatus.APPLIED,
            },
            {
                "post": True,
                "url": reject_url,
                "text": "Reject",
                "color": "danger",
                "icon": "bi bi-x-square-fill",
                "disable": record.status
                in [
                    ProgramApplicationStatus.ACCEPTED,
                    ProgramApplicationStatus.REJECTED,
                    ProgramApplicationStatus.DECLINED,
                ],
            },
        ]
        return get_manage_buttons_html(buttons, self.context["request"])

    class Meta:
        model = ProgramApplication
        fields = ("organization", "created_by", "date_modified", "status", "manage")
        attrs = RESPONSIVE_TABLE_AND_LIGHT_HEADER
        template_name = TABLE_TEMPLATE
        empty_text = "No applications yet."
        orderable = False


class ProgramTable(tables.Table):
    name = tables.Column()
    start_date = tables.DateColumn()
    end_date = tables.DateColumn()
    delivery_type = tables.Column(orderable=False)

    manage = tables.Column(
        verbose_name=_("Manage"),
        orderable=False,
        empty_values=(),
    )

    def render_budget(self, record):
        return f"{record.budget} {record.currency}"

    def render_manage(self, record):
        edit_url = reverse(
            "program:edit",
            kwargs={"org_slug": self.context["request"].org.slug, "pk": record.id},
        )
        view_opp_url = reverse(
            "program:opportunity_list",
            kwargs={
                "org_slug": self.context["request"].org.slug,
                "pk": record.id,
            },
        )

        dashboard_url = reverse(
            "program:dashboard",
            kwargs={
                "org_slug": self.context["request"].org.slug,
                "pk": record.id,
            },
        )
        application_url = reverse(
            "program:applications",
            kwargs={
                "org_slug": self.context["request"].org.slug,
                "pk": record.id,
            },
        )
        buttons = [
            {
                "post": False,
                "url": edit_url,
                "text": "Edit",
                "color": "warning",
                "icon": "bi bi-check-circle-fill",
            },
            {
                "post": False,
                "url": view_opp_url,
                "text": "View opportunities",
                "icon": "bi bi-eye",
                "disable": ProgramApplication.objects.filter(
                    program=record, status=ProgramApplicationStatus.ACCEPTED
                ).count()
                == 0,
            },
            {
                "post": False,
                "url": application_url,
                "text": "Applications",
                "color": "success",
                "icon": "bi bi-people-fill",
            },
            {"post": False, "url": dashboard_url, "text": "Dashboard", "color": "info", "icon": "bi bi-graph-up"},
        ]
        return get_manage_buttons_html(buttons, self.context["request"])

    class Meta:
        model = Program
        fields = (
            "name",
            "start_date",
            "end_date",
            "delivery_type",
            "budget",
            "manage",
        )
        template_name = TABLE_TEMPLATE
        attrs = RESPONSIVE_TABLE_AND_LIGHT_HEADER
        empty_text = "No programs yet."
        orderable = False


def get_manage_buttons_html(buttons, request):
    context = {
        "buttons": buttons,
    }
    html = render_to_string(
        "tables/table_manage_action.html",
        context,
        request=request,
    )
    return mark_safe(html)


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
