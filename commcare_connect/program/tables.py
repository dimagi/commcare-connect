import django_tables2 as tables
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _

from .models import ManagedOpportunityApplication, ManagedOpportunityApplicationStatus, Program

TABLE_TEMPLATE = "django_tables2/bootstrap5.html"
RESPONSIVE_TABLE_AND_LIGHT_HEADER = {
    "class": "table border table-responsive",
    "thead": {"class": "table-light"},
}


class OpportunityInvitationTable(tables.Table):
    name = tables.Column(accessor="managed_opportunity.name", verbose_name=_("Name"))
    start_date = tables.DateColumn(
        accessor="managed_opportunity.start_date", verbose_name=_("Start Date"), default=_("Not Set")
    )
    end_date = tables.DateColumn(
        accessor="managed_opportunity.end_date", verbose_name=_("End Date"), default=_("Not Set")
    )
    status = tables.Column(verbose_name=_("Status"))

    manage = tables.Column(
        verbose_name=_("Manage"),
        orderable=False,
        empty_values=(),
    )

    def render_manage(self, record):
        apply_url = reverse(
            "opportunity:apply_or_decline_application",
            kwargs={
                "org_slug": self.context["request"].org.slug,
                "pk": record.managed_opportunity.id,
                "application_id": record.id,
                "action": "apply",
            },
        )
        decline_url = reverse(
            "opportunity:apply_or_decline_application",
            kwargs={
                "org_slug": self.context["request"].org.slug,
                "pk": record.managed_opportunity.id,
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
        model = ManagedOpportunityApplication
        fields = ("name", "start_date", "end_date", "status", "manage")
        order_by_field = "invite_sort"
        attrs = RESPONSIVE_TABLE_AND_LIGHT_HEADER
        template_name = TABLE_TEMPLATE
        order_by = ("date_modified",)


class ManagedOpportunityApplicationTable(tables.Table):
    organization = tables.Column()
    created_by = tables.Column(orderable=False)
    status = tables.Column()
    date_modified = tables.DateColumn(verbose_name=_("Updated On"), order_by=("date_modified",))
    manage = tables.Column(
        verbose_name=_("Manage"),
        orderable=False,
        empty_values=(),
    )

    def render_manage(self, record):
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
                "disable": record.status != ManagedOpportunityApplicationStatus.APPLIED,
            },
            {
                "post": True,
                "url": reject_url,
                "text": "Reject",
                "color": "danger",
                "icon": "bi bi-x-square-fill",
                "disable": record.status
                in [
                    ManagedOpportunityApplicationStatus.ACCEPTED,
                    ManagedOpportunityApplicationStatus.REJECTED,
                    ManagedOpportunityApplicationStatus.DECLINED,
                ],
            },
        ]
        return get_manage_buttons_html(buttons, self.context["request"])

    class Meta:
        model = ManagedOpportunityApplication
        fields = ("organization", "created_by", "date_modified", "status", "manage")
        attrs = RESPONSIVE_TABLE_AND_LIGHT_HEADER
        template_name = TABLE_TEMPLATE
        order_by = ("date_modified",)
        empty_text = "No applications yet."


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
            },
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
        order_by = ("name",)
        empty_text = "No programs yet."


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
