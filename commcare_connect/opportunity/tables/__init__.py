from urllib.parse import urlencode

import django_tables2 as tables
from django.contrib.humanize.templatetags.humanize import intcomma
from django.db.models import CharField, Value
from django.db.models.functions import Coalesce, NullIf
from django.template.defaultfilters import linebreaksbr
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django_tables2 import columns

from commcare_connect.opportunity.models import (
    AssignedTask,
    AssignedTaskStatus,
    BillToAddress,
    CompletedWork,
    DeliverUnit,
    DeliveryType,
    LearnModule,
    OpportunityAccess,
    OpportunityStatus,
    PaymentUnit,
    TaskType,
    TaskTypeModeChoices,
    UserInvite,
    UserInviteStatus,
    UserVisit,
    VisitValidationStatus,
)
from commcare_connect.program.utils import AccessLevel, opportunity_access_level_from_request
from commcare_connect.utils.tables import (
    STOP_CLICK_PROPAGATION_ATTR,
    DMYTColumn,
    IndexColumn,
    ManagedOptionTable,
    OrgContextTable,
    merge_attrs,
    select_column,
)


def header_with_tooltip(label, tooltip_text):
    return format_html(
        '<span x-data x-tooltip.raw="{}">{}</span>',
        tooltip_text,
        label,
    )


def value_with_icon_tooltip(display_value, tooltip_html, theme=None):
    """Render `display_value` followed by an info icon carrying a rich HTML tooltip.

    `tooltip_html` should come from a rendered template (see e.g. `exchange_rate_tooltip.html`),
    not be built up as a string in Python.
    """
    theme_modifier = f".theme.{theme}" if theme else ""
    return format_html(
        '{} <i class="fa-solid fa-circle-info text-gray-400 ml-1" '
        'x-data x-tooltip.raw.html.interactive{theme_modifier}="{tooltip}"></i>',
        display_value,
        theme_modifier=mark_safe(theme_modifier),
        tooltip=mark_safe(tooltip_html),
    )


class OpportunityContextTable(OrgContextTable):
    def __init__(self, *args, **kwargs):
        self.opp_id = kwargs.pop("opp_id", None)
        super().__init__(*args, **kwargs)


def show_warning(record):
    if record.status not in (VisitValidationStatus.approved, VisitValidationStatus.rejected):
        if record.flagged:
            return "table-warning"
    return ""


class UserVisitTable(OrgContextTable):
    # export only columns
    visit_id = columns.Column("Visit ID", accessor="xform_id", visible=False)
    username = columns.Column("Username", accessor="user__username", visible=False)
    form_json = columns.Column("Form JSON", accessor="form_json", visible=False)
    visit_date_export = DMYTColumn(verbose_name="Visit date", accessor="visit_date", visible=False)
    reason = columns.Column("Rejected Reason", accessor="reason", visible=False)
    justification = columns.Column("Justification", accessor="justification", visible=False)
    duration = columns.Column("Duration", accessor="duration", visible=False)
    entity_id = columns.Column("Entity ID", accessor="entity_id", visible=False)
    payment_unit = columns.Column("Payment Unit", accessor="completed_work__payment_unit__name", visible=False)
    over_limit_reasons = columns.Column("Over Limit Reason", accessor="over_limit_reasons", visible=False)

    deliver_unit = columns.Column("Unit Name", accessor="deliver_unit__name")
    entity_name = columns.Column("Entity Name", accessor="entity_name")
    flag_reason = columns.Column("Flags", accessor="flag_reason", empty_values=({}, None))
    details = columns.Column(verbose_name="", empty_values=())

    def value_visit_date_export(self, value):
        return value.isoformat() if value else value

    def render_details(self, record):
        url = reverse(
            "opportunity:visit_verification",
            kwargs={"org_slug": self.org_slug, "pk": record.pk},
        )
        return mark_safe(f'<a class="btn btn-sm btn-primary" href="{url}">Review</a>')

    def render_flag_reason(self, value):
        short = [flag[1] for flag in value.get("flags")]
        return ", ".join(short)

    def value_over_limit_reasons(self, record):
        return "; ".join(record.get_over_limit_reasons_display())

    class Meta:
        model = UserVisit
        fields = ("user__name", "username", "visit_date", "status", "review_status")
        sequence = (
            "visit_id",
            "visit_date",
            "visit_date_export",
            "status",
            "review_status",
            "username",
            "user__name",
            "deliver_unit",
            "payment_unit",
            "over_limit_reasons",
        )
        empty_text = "No forms."
        orderable = False
        row_attrs = {"class": show_warning}


class AggregateColumn(columns.Column):
    def render_footer(self, bound_column, table):
        return sum(1 if bound_column.accessor.resolve(row) else 0 for row in table.data)


class SumColumn(columns.Column):
    def render_footer(self, bound_column, table):
        return sum(bound_column.accessor.resolve(row) or 0 for row in table.data)


class BooleanAggregateColumn(columns.BooleanColumn, AggregateColumn):
    pass


class UserStatusTable(OrgContextTable):
    display_name = columns.Column(verbose_name="Name", footer="Total", empty_values=())
    username = columns.Column(accessor="opportunity_access__user__username", visible=False)
    claimed = AggregateColumn(verbose_name="Job Claimed", accessor="job_claimed")
    status = columns.Column(
        footer=lambda table: f"Accepted: {sum(invite.status == UserInviteStatus.accepted for invite in table.data)}",
    )
    started_learning = AggregateColumn(
        verbose_name="Started Learning", accessor="opportunity_access__date_learn_started"
    )
    completed_learning = AggregateColumn(verbose_name="Completed Learning", accessor="date_learn_completed")
    passed_assessment = BooleanAggregateColumn(verbose_name="Passed Assessment")
    started_delivery = AggregateColumn(verbose_name="Started Delivery", accessor="date_deliver_started")
    last_visit_date = columns.Column(accessor="last_visit_date_d")
    view_profile = columns.Column("", empty_values=(), footer=lambda table: f"Invited: {len(table.rows)}")

    class Meta:
        model = UserInvite
        fields = ("status",)
        sequence = (
            "display_name",
            "username",
            "status",
            "started_learning",
            "completed_learning",
            "passed_assessment",
            "claimed",
            "started_delivery",
            "last_visit_date",
        )
        empty_text = "No users invited for this opportunity."
        orderable = False

    def render_display_name(self, record):
        if not getattr(record.opportunity_access, "accepted", False):
            return "---"
        return record.opportunity_access.display_name

    def render_started_learning(self, record, value):
        return date_with_time_popup(self, value)

    def render_completed_learning(self, record, value):
        return date_with_time_popup(self, value)

    def render_started_delivery(self, record, value):
        return date_with_time_popup(self, value)

    def render_last_visit_date(self, record, value):
        return date_with_time_popup(self, value)


class DeliverStatusTable(OrgContextTable):
    display_name = columns.Column(verbose_name="Name of the User", footer="Total")
    username = columns.Column(accessor="user__username", visible=False)
    payment_unit = columns.Column("Name of Payment Unit")
    delivered = SumColumn("Delivered")
    pending = SumColumn("Pending")
    approved = SumColumn("Approved")
    rejected = SumColumn("Rejected")
    over_limit = SumColumn("Over Limit")
    incomplete = SumColumn("Incomplete")
    details = columns.Column(verbose_name="", empty_values=())

    class Meta:
        model = OpportunityAccess
        orderable = False
        fields = ("display_name",)
        sequence = (
            "display_name",
            "username",
            "payment_unit",
            "delivered",
            "pending",
            "approved",
            "rejected",
            "over_limit",
            "incomplete",
        )

    def render_details(self, record):
        base_url = reverse(
            "opportunity:user_visits_list",
            kwargs={"org_slug": self.org_slug, "opp_id": record.opportunity.opportunity_id},
        )
        url = f"{base_url}?{urlencode({'user': record.user.user_id})}"
        return mark_safe(f'<a href="{url}">View Details</a>')

    def render_last_visit_date(self, record, value):
        return date_with_time_popup(self, value)


class CompletedWorkTable(tables.Table):
    id = columns.Column("Instance Id", visible=False)
    username = columns.Column(accessor="opportunity_access__user__username", visible=False)
    phone_number = columns.Column(accessor="opportunity_access__user__phone_number", visible=False)
    entity_id = columns.Column(visible=False)
    reason = columns.Column("Rejected Reason", accessor="reason", visible=False)
    display_name = columns.Column("Name of the User", accessor="opportunity_access__display_name")
    payment_unit = columns.Column("Payment Unit", accessor="payment_unit__name")
    status = columns.Column("Payment Approval")

    class Meta:
        model = CompletedWork
        fields = (
            "entity_id",
            "entity_name",
            "status",
            "reason",
            "completion_date",
            "flags",
        )
        orderable = False
        sequence = (
            "id",
            "username",
            "phone_number",
            "display_name",
            "entity_id",
            "entity_name",
            "payment_unit",
            "completion_date",
            "flags",
            "status",
            "reason",
        )

    def render_flags(self, record, value):
        return ", ".join(value)

    def render_completion_date(self, record, value):
        return date_with_time_popup(self, value)


class SuspendedUsersTable(tables.Table):
    display_name = columns.Column("Name of the User")
    revoke_suspension = columns.TemplateColumn("Revoke")

    class Meta:
        model = OpportunityAccess
        fields = ("display_name", "suspension_date", "suspension_reason", "revoke_suspension")
        orderable = False
        empty_text = "No suspended users."

    def __init__(self, *args, **kwargs):
        self.has_suspension_perm = kwargs.pop("has_suspension_perm", False)
        super().__init__(*args, **kwargs)

    def render_suspension_date(self, record, value):
        return date_with_time_popup(self, value)

    def render_revoke_suspension(self, record, value):
        revoke_url = reverse(
            "opportunity:revoke_user_suspension",
            args=(
                self.context.request.org.slug,
                record.opportunity.opportunity_id,
                record.opportunity_access_id,
            ),
        )
        page_url = reverse(
            "opportunity:suspended_users_list",
            args=(self.context.request.org.slug, record.opportunity.opportunity_id),
        )
        return render_to_string(
            "opportunity/partials/revoke_suspension.html",
            {"revoke_url": revoke_url, "page_url": page_url, "has_suspension_perm": self.has_suspension_perm},
            request=self.context.request,
        )


class UserVisitReviewTable(OrgContextTable):
    pk = columns.CheckBoxColumn(
        accessor="pk",
        verbose_name="",
        attrs={
            "input": {"x-model": "selected"},
            "th__input": {"@click": "toggleSelectAll()", "x-bind:checked": "selectAll"},
        },
    )
    visit_id = columns.Column("Visit ID", accessor="xform_id", visible=False)
    username = columns.Column(accessor="user__username", verbose_name="Username")
    name = columns.Column(accessor="user__name", verbose_name="Name of the User", orderable=True)
    justification = columns.Column(verbose_name="Justification")
    visit_date = columns.Column(orderable=True)
    created_on = columns.Column(accessor="review_created_on", verbose_name="Review Requested On")
    review_status = columns.Column(verbose_name="Program Manager Review", orderable=True)
    user_visit = columns.Column(verbose_name="User Visit", empty_values=())

    class Meta:
        model = UserVisit
        orderable = False
        fields = (
            "pk",
            "username",
            "name",
            "status",
            "justification",
            "visit_date",
            "review_status",
            "created_on",
            "user_visit",
            "flag_reason",
        )
        empty_text = "No visits submitted for review."

    def render_user_visit(self, record):
        url = reverse(
            "opportunity:visit_verification",
            kwargs={"org_slug": self.org_slug, "pk": record.pk},
        )
        return mark_safe(f'<a href="{url}">View</a>')

    def render_flag_reason(self, value):
        short = [flag[1] for flag in value.get("flags")]
        return ", ".join(short)


class PaymentReportTable(tables.Table):
    payment_unit = columns.Column(verbose_name="Payment Unit")
    approved = SumColumn(verbose_name="Approved Units")
    user_payment_accrued = SumColumn(verbose_name="User Payment Accrued")
    nm_payment_accrued = SumColumn(verbose_name="Network Manager Payment Accrued")

    class Meta:
        orderable = False


def popup_html(value, popup_title, popup_direction="top", popup_class="", popup_attributes=""):
    return format_html(
        "<span class='{}' data-bs-toggle='tooltip' data-bs-placement='{}' data-bs-title='{}' {}>{}</span>",
        popup_class,
        popup_direction,
        popup_title,
        popup_attributes,
        value,
    )


def date_with_time_popup(table, date):
    if table.exclude and "date_popup" in table.exclude:
        return date
    return popup_html(
        date.strftime("%d %b, %Y"),
        date.strftime("%d %b %Y, %I:%M%p"),
    )


STAT_COLUMN_ATTRS = {"th": {"class": "col-stat"}, "td": {"class": "col-stat"}}


class BaseOpportunityList(OrgContextTable):
    stats_style = "underline underline-offset-2 justify-center"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.use_view_url = False

    index = IndexColumn()
    opportunity = tables.Column(accessor="name")
    entity_type = tables.TemplateColumn(
        verbose_name="",
        orderable=False,
        template_code="""
                <div class="flex justify-start text-sm font-normal text-brand-deep-purple w-fit"
                     x-data="{
                       showTooltip: false,
                       tooltipStyle: '',
                       positionTooltip(el) {
                         const rect = el.getBoundingClientRect();
                         const top = rect.top - 30;  /* 30px above the icon */
                         const left = rect.left + rect.width/2;
                         this.tooltipStyle = `top:${top}px; left:${left}px; transform:translateX(-50%)`;
                       }
                     }">
                    {% if record.is_test %}
                        <div class="relative">
                            <i class="fa-solid fa-file-circle-exclamation"
                               @mouseenter="showTooltip = true; positionTooltip($el)"
                               @mouseleave="showTooltip = false
                               "></i>
                            <span x-show="showTooltip"
                                  :style="tooltipStyle"
                                  class="fixed z-50 bg-white shadow-sm text-brand-deep-purple text-xs py-0.5 px-4 rounded-lg whitespace-nowrap">
                                Test Opportunity
                            </span>
                        </div>
                    {% else %}
                        <span class="relative">
                            <i class="invisible fa-solid fa-file-circle-exclamation"></i>
                        </span>
                    {% endif %}
                </div>
            """,  # noqa: E501
    )

    status = tables.Column(verbose_name="Status", accessor="status", orderable=True)

    program = tables.Column(accessor="program_name")
    delivery_type = tables.Column(
        verbose_name=gettext_lazy("Delivery Type"), accessor="delivery_type_name", empty_values=()
    )
    start_date = DMYTColumn()
    end_date = DMYTColumn()

    class Meta:
        sequence = (
            "index",
            "opportunity",
            "entity_type",
            "status",
            "program",
            "delivery_type",
            "start_date",
            "end_date",
        )
        order_by = ("status", "-start_date", "end_date")
        empty_text = gettext_lazy("No Opportunities created yet.")

    STATUS_BADGE_CLASSES = {
        OpportunityStatus.ACTIVE: "badge badge-sm bg-green-600/20 text-green-600",
        OpportunityStatus.ENDED: "badge badge-sm bg-orange-600/20 text-orange-600",
        OpportunityStatus.INACTIVE: "badge badge-sm bg-slate-100 text-slate-400",
    }

    def render_status(self, value):
        status = OpportunityStatus(value)
        badge_class = self.STATUS_BADGE_CLASSES[status]
        text = status.label

        return format_html(
            '<div class="flex justify-start text-sm font-normal truncate text-brand-deep-purple overflow-clip overflow-ellipsis">'  # noqa: E501
            '  <span class="{}">{}</span>'
            "</div>",
            badge_class,
            text,
        )

    def format_date(self, date):
        return date.strftime("%d-%b-%Y") if date else "--"

    def _render_div(self, value, extra_classes=""):
        base_classes = "flex text-sm font-normal truncate text-brand-deep-purple overflow-clip overflow-ellipsis"
        all_classes = f"{base_classes} {extra_classes}".strip()
        return format_html('<div class="{}">{}</div>', all_classes, value)

    def render_opportunity(self, value, record):
        url = reverse("opportunity:detail", args=(self.org_slug, record.opportunity_id))
        value = format_html('<a href="{}">{}</a>', url, value)
        return self._render_div(value, extra_classes="justify-start text-wrap")

    def render_program(self, value):
        return self._render_div(value if value else "--", extra_classes="justify-start")

    render_delivery_type = render_program

    def render_actions(self, record):
        actions = [
            {
                "title": "View Opportunity",
                "url": reverse("opportunity:detail", args=[self.org_slug, record.opportunity_id]),
            },
            {
                "title": "View Connect Workers",
                "url": reverse("opportunity:worker_list", args=[self.org_slug, record.opportunity_id]),
            },
        ]
        if opportunity_access_level_from_request(self.request, record) >= AccessLevel.STANDARD:
            actions.append(
                {
                    "title": "View Invoices",
                    "url": reverse("opportunity:invoice_list", args=[self.org_slug, record.opportunity_id]),
                }
            )

        html = render_to_string(
            "components/dropdowns/text_button_dropdown.html",
            context={
                "text": "...",
                "list": actions,
                "styles": "text-sm",
            },
        )
        return mark_safe(html)

    def render_worker_list_url_column(self, value, opp_id, url_slug="worker_list", sort=None):
        url = reverse(f"opportunity:{url_slug}", args=(self.org_slug, opp_id))

        if sort:
            url += "?" + sort
        value = format_html('<a href="{}">{}</a>', url, value)
        return self._render_div(value, extra_classes=self.stats_style)


class OpportunityTable(BaseOpportunityList):
    col_attrs = merge_attrs(STAT_COLUMN_ATTRS, STOP_CLICK_PROPAGATION_ATTR)

    pending_invites = tables.Column(
        verbose_name=header_with_tooltip(
            "Pending Invites", "Connect Workers not yet clicked on invite link or started learning in app"
        ),
        attrs=col_attrs,
        orderable=False,
    )
    inactive_workers = tables.Column(
        verbose_name=header_with_tooltip(
            "Inactive Connect Workers", "Did not submit a Learn or Deliver form in 3 day"
        ),
        attrs=col_attrs,
        orderable=False,
    )
    pending_approvals = tables.Column(
        verbose_name=header_with_tooltip(
            "Pending Approvals", "Deliveries that are flagged and require NM or PM approval"
        ),
        attrs=col_attrs,
        orderable=False,
    )
    payments_due = tables.Column(
        verbose_name=header_with_tooltip("Payments Due", "Worker payments accrued minus the amount paid"),
        attrs=col_attrs,
        orderable=False,
    )
    actions = tables.Column(empty_values=(), orderable=False, verbose_name="", attrs=STOP_CLICK_PROPAGATION_ATTR)

    class Meta(BaseOpportunityList.Meta):
        sequence = BaseOpportunityList.Meta.sequence + (
            "pending_invites",
            "inactive_workers",
            "pending_approvals",
            "payments_due",
            "actions",
        )

    def render_pending_invites(self, value, record):
        return self.render_worker_list_url_column(value=value, opp_id=record.opportunity_id)

    def render_inactive_workers(self, value, record):
        return self.render_worker_list_url_column(value=value, opp_id=record.opportunity_id, sort="sort=last_active")

    def render_pending_approvals(self, value, record):
        return self.render_worker_list_url_column(
            value=value, opp_id=record.opportunity_id, url_slug="worker_deliver", sort="sort=-pending"
        )

    def render_payments_due(self, value, record):
        if value is None:
            value = 0

        value = f"{record.currency_code} {intcomma(value)}"
        return self.render_worker_list_url_column(
            value=value, opp_id=record.opportunity_id, url_slug="worker_payments", sort="sort=-total_paid"
        )


class ProgramManagerOpportunityTable(BaseOpportunityList):
    active_workers = tables.Column(
        verbose_name=header_with_tooltip(
            "Active Connect Workers", "Worker delivered a Learn or Deliver form in the last 3 days"
        ),
        attrs=STAT_COLUMN_ATTRS,
        orderable=False,
    )
    total_deliveries = tables.Column(
        verbose_name=header_with_tooltip("Total Deliveries", "Payment units completed"),
        attrs=STAT_COLUMN_ATTRS,
        orderable=False,
    )
    verified_deliveries = tables.Column(
        verbose_name=header_with_tooltip("Verified Deliveries", "Payment units fully approved by PM and NM"),
        attrs=STAT_COLUMN_ATTRS,
        orderable=False,
    )
    worker_earnings = tables.Column(
        verbose_name=header_with_tooltip("Worker Earnings", "Total payment accrued to worker"),
        accessor="total_accrued",
        attrs=STAT_COLUMN_ATTRS,
        orderable=False,
    )
    actions = tables.Column(empty_values=(), orderable=False, verbose_name="")

    class Meta(BaseOpportunityList.Meta):
        sequence = BaseOpportunityList.Meta.sequence + (
            "active_workers",
            "total_deliveries",
            "verified_deliveries",
            "worker_earnings",
            "actions",
        )

    def render_active_workers(self, value, record):
        return self.render_worker_list_url_column(value=value, opp_id=record.opportunity_id)

    def render_total_deliveries(self, value, record):
        return self.render_worker_list_url_column(
            value=value, opp_id=record.opportunity_id, url_slug="worker_deliver", sort="sort=-delivered"
        )

    def render_verified_deliveries(self, value, record):
        return self.render_worker_list_url_column(
            value=value, opp_id=record.opportunity_id, url_slug="worker_deliver", sort="sort=-approved"
        )

    def render_worker_earnings(self, value, record):
        url = reverse("opportunity:worker_payments", args=(self.org_slug, record.opportunity_id))
        url += "?sort=-payment_accrued"
        value = f"{record.currency_code} {intcomma(value)}"
        value = format_html('<a href="{}">{}</a>', url, value)
        return self._render_div(value, extra_classes=self.stats_style)

    def render_opportunity(self, record):
        url = reverse("opportunity:detail", args=(self.org_slug, record.opportunity_id))
        html = format_html(
            """
            <a href={} class="flex flex-col items-start text-wrap w-50">
                <p class="text-sm text-slate-900">{}</p>
                <p class="text-xs text-slate-400">{}</p>
            </a>
            """,
            url,
            record.name,
            record.organization.name,
        )
        return html


class StatusIndicatorColumn(tables.Column):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("verbose_name", "Status")
        super().__init__(*args, **kwargs)

    def _is_suspended(self, record):
        opportunity_access = getattr(record, "opportunity_access", None)
        if opportunity_access:
            return opportunity_access.suspended
        return getattr(record, "suspended", False)

    def render(self, record):
        if self._is_suspended(record):
            return format_html(
                '<span x-data x-tooltip.raw="{}"><i class="fa-solid fa-minus-square text-black-600"></i></span>',
                _("User suspended"),
            )

        if record.status == UserInviteStatus.accepted:
            return format_html(
                '<span x-data x-tooltip.raw="{}"><i class="fa-solid fa-circle-check text-green-600"></i></span>',
                _("Invite accepted"),
            )
        elif record.status in [UserInviteStatus.invited, UserInviteStatus.sms_delivered]:
            return format_html(
                '<span x-data x-tooltip.raw="{}"><i class="fa-regular fa-clock text-orange-600"></i></span>',
                _("Invite pending"),
            )

        if record.status in [UserInviteStatus.not_found, UserInviteStatus.sms_not_delivered]:
            return format_html(
                '<span x-data x-tooltip.raw="{}"><i class="fa-solid fa-circle-xmark text-red-600"></i></span>',
                _("User not found") if record.status == UserInviteStatus.not_found else _("Invite failed"),
            )


class TaskStatusColumn(tables.Column):
    def render(self, value):
        if value is None:
            return "—"
        if value == AssignedTaskStatus.ASSIGNED:
            status = _("To Do")
            badge_classes = "bg-amber-100 text-amber-800"
        elif value == AssignedTaskStatus.COMPLETED:
            status = _("Complete")
            badge_classes = "bg-green-100 text-green-800"
        else:
            return str(value)
        return format_html(
            '<span class="inline-flex w-[80px] items-center justify-center px-3 py-1 rounded text-xs font-medium {}">'
            "{}</span>",
            badge_classes,
            status,
        )


class LearnModuleTable(tables.Table):
    index = IndexColumn()
    name = tables.Column(verbose_name=_("Module Name"))
    description = tables.Column(verbose_name=_("Module Description"))

    class Meta:
        model = LearnModule
        orderable = False
        fields = ("index", "name", "description", "time_estimate")
        empty_text = "No Learn Module for this opportunity."

    def render_time_estimate(self, value):
        return f"{value}hr"


class DeliverUnitTable(tables.Table):
    index = IndexColumn(empty_values=(), verbose_name="#")

    slug = tables.Column(verbose_name="Delivery Unit ID")
    name = tables.Column(verbose_name="Name")

    class Meta:
        model = DeliverUnit
        orderable = False
        fields = ("index", "slug", "name")
        empty_text = "No Deliver units for this opportunity."


class PaymentUnitTable(OrgContextTable):
    index = IndexColumn()
    name = tables.Column(verbose_name="Payment Unit Name")
    max_total = tables.Column(verbose_name="Total Deliveries")
    max_daily = tables.Column(default=gettext_lazy("No limit"))
    deliver_units = tables.Column(verbose_name="Delivery Units")
    amount = tables.Column(verbose_name="Worker pay per delivery")
    org_amount = tables.Column(verbose_name="Org pay per delivery")

    def __init__(self, *args, **kwargs):
        self.can_edit = kwargs.pop("is_program_manager", False)
        if not self.can_edit:
            kwargs["exclude"] = "org_amount"
        super().__init__(*args, **kwargs)

    class Meta:
        model = PaymentUnit
        orderable = False
        fields = (
            "index",
            "name",
            "start_date",
            "end_date",
            "amount",
            "org_amount",
            "max_total",
            "max_daily",
            "deliver_units",
        )
        empty_text = "No payment units for this opportunity."

    def render_deliver_units(self, record):
        deliver_units = record.deliver_units.all()
        count = deliver_units.count()

        if self.can_edit:
            edit_url = reverse(
                "opportunity:edit_payment_unit",
                args=(self.org_slug, record.opportunity.opportunity_id, record.payment_unit_id),
            )
        else:
            edit_url = None

        context = {
            "count": count,
            "deliver_units": deliver_units,
            "edit_url": edit_url,
        }
        return render_to_string("opportunity/extendable_payment_unit_row.html", context)


_task_select_td_extra = {
    "@change": "updateSelectAll()",
    # return None instead of False to skip the attribute.
    "disabled": lambda record: True if record.status == AssignedTaskStatus.COMPLETED else None,
}


class BaseAssignedTaskTable(tables.Table):
    select = select_column(td_extra=_task_select_td_extra)
    status = TaskStatusColumn(verbose_name=gettext_lazy("Status"), accessor="status")
    assigned_date = DMYTColumn(verbose_name=gettext_lazy("Assigned Date"), accessor="date_created")
    due_date = DMYTColumn(verbose_name=gettext_lazy("Due Date"), accessor="due_date")
    assigned_by = tables.Column(
        verbose_name=gettext_lazy("Assigned By"),
        accessor="assigned_by",
        empty_values=(None,),
        default=gettext_lazy("Deleted user"),
    )

    def render_assigned_by(self, value):
        return value.name or value.email

    def order_assigned_by(self, queryset, is_descending):
        expression = Coalesce(NullIf("assigned_by__name", Value("")), "assigned_by__email", output_field=CharField())
        queryset = queryset.order_by(expression.desc() if is_descending else expression.asc())
        return queryset, True


class AssignedTaskListTable(BaseAssignedTaskTable, OpportunityContextTable):
    connect_worker = tables.Column(verbose_name=gettext_lazy("Connect Worker"), accessor="opportunity_access__user")
    phone_number = tables.Column(
        verbose_name=gettext_lazy("Phone Number"),
        accessor="opportunity_access__user__phone_number",
        default="—",
    )
    task_type = tables.Column(verbose_name=gettext_lazy("Task Type"), accessor="task_type__name")
    action = tables.TemplateColumn(
        verbose_name="",
        orderable=False,
        template_name="opportunity/assigned_task_edit_button.html",
    )

    def __init__(self, *args, can_edit_tasks=False, can_delete_tasks=False, **kwargs):
        super().__init__(*args, **kwargs)
        if not can_edit_tasks:
            self.columns.hide("action")
        if not can_delete_tasks:
            self.columns.hide("select")

    class Meta:
        model = AssignedTask
        fields = ()
        sequence = (
            "select",
            "connect_worker",
            "phone_number",
            "status",
            "task_type",
            "assigned_date",
            "due_date",
            "assigned_by",
            "action",
        )
        order_by = ("-assigned_date",)
        row_attrs = {"class": "group"}
        empty_text = gettext_lazy("No tasks have been assigned yet.")

    def render_connect_worker(self, value):
        return format_html(
            '<div class="flex flex-col items-start">'
            '<p class="text-sm font-medium text-gray-700">{}</p>'
            '<p class="text-xs text-gray-500">{}</p>'
            "</div>",
            value.name,
            value.username,
        )


class TaskTable(OpportunityContextTable):
    index = IndexColumn()
    name = tables.Column(verbose_name=gettext_lazy("Task Type Name"))
    description = tables.Column(verbose_name=gettext_lazy("Description"))
    mode = tables.Column(verbose_name=gettext_lazy("Mode"))
    linked_task_unit = tables.Column(verbose_name=gettext_lazy("Linked Task Unit"), accessor="unit_name")
    archived = tables.DateColumn(verbose_name=gettext_lazy("Archived"))
    actions = tables.TemplateColumn(
        verbose_name=gettext_lazy("Actions"),
        orderable=False,
        template_code="""
            {% load i18n %}
            <button
                type="button"
                class="button button-md outline-style"
                hx-get="{% url 'opportunity:edit_task_type' table.org_slug table.opp_id record.pk %}"
                hx-target="#edit-task-form"
                @htmx:after-request="if ($event.detail.successful) {
                        showEditTaskTypeModal = true; editTaskError = false
                    } else { editTaskError = true }">
                <i class="fa-regular fa-pen-to-square mr-1"></i> {% translate "Edit" %}
            </button>
        """,
    )

    class Meta:
        model = TaskType
        fields = ("index", "name", "description", "mode", "linked_task_unit", "archived", "actions")
        empty_text = gettext_lazy("No task types configured for this opportunity.")

    def render_mode(self, value, record):
        if value == TaskTypeModeChoices.OCS:
            badge_class = "badge badge-sm bg-indigo-600/20 text-indigo-600"
        else:
            badge_class = "badge badge-sm bg-slate-100 text-slate-500"
        return format_html('<span class="{}">{}</span>', badge_class, record.get_mode_display())


class DeliveryTypeTable(ManagedOptionTable):
    program_count = columns.Column(verbose_name=gettext_lazy("Programs"), attrs=STAT_COLUMN_ATTRS)
    opportunity_count = columns.Column(verbose_name=gettext_lazy("Opportunities"), attrs=STAT_COLUMN_ATTRS)

    class Meta(ManagedOptionTable.Meta):
        model = DeliveryType


class BillToAddressTable(ManagedOptionTable):
    name = columns.Column(verbose_name=gettext_lazy("Entity name"))
    address = columns.Column(
        verbose_name=gettext_lazy("Address"), orderable=False, attrs={"td": {"class": "whitespace-normal"}}
    )
    invoice_count = columns.Column(verbose_name=gettext_lazy("Invoices"), attrs=STAT_COLUMN_ATTRS)
    is_default = columns.TemplateColumn(
        verbose_name=gettext_lazy("Default"), template_name="users/bill_to_addresses/default_column.html"
    )
    is_active = columns.BooleanColumn(verbose_name=gettext_lazy("Active"), attrs=STAT_COLUMN_ATTRS)

    class Meta(ManagedOptionTable.Meta):
        model = BillToAddress
        fields = ("name", "address", "invoice_count", "is_default", "is_active")
        exclude = ("slug", "description")
        sequence = (*fields, "actions")

    def render_address(self, value):
        return linebreaksbr(value)
