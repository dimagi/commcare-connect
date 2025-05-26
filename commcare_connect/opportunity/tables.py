import itertools
from urllib.parse import urlencode

import django_tables2 as tables
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Column, Layout, Row
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.utils.timezone import localtime
from django_filters import ChoiceFilter, DateRangeFilter, FilterSet, ModelChoiceFilter
from django_tables2 import columns, utils

from commcare_connect.opportunity.models import (
    CatchmentArea,
    CompletedWork,
    DeliverUnit,
    LearnModule,
    CompletedWorkStatus,
    OpportunityAccess,
    Payment,
    PaymentInvoice,
    PaymentUnit,
    UserInvite,
    UserInviteStatus,
    UserVisit,
    VisitReviewStatus,
    VisitValidationStatus,
)
from commcare_connect.users.models import User
from commcare_connect.utils.tables import (
    STOP_CLICK_PROPAGATION_ATTR,
    TEXT_CENTER_ATTR,
    DMYTColumn,
    DurationColumn,
    IndexColumn,
    OrgContextTable,
    merge_attrs,
)
from django.contrib.humanize.templatetags.humanize import intcomma


class OpportunityContextTable(OrgContextTable):
    def __init__(self, *args, **kwargs):
        self.opp_id = kwargs.pop("opp_id", None)
        super().__init__(*args, **kwargs)


class LearnStatusTable(OrgContextTable):
    display_name = columns.Column(verbose_name="Name")
    learn_progress = columns.Column(verbose_name="Modules Completed")
    assessment_count = columns.Column(verbose_name="Number of Attempts")
    assessment_status = columns.Column(verbose_name="Assessment Status")
    details = columns.Column(verbose_name="", empty_values=())

    class Meta:
        model = OpportunityAccess
        fields = ("display_name", "learn_progress", "assessment_status", "assessment_count")
        sequence = ("display_name", "learn_progress")
        orderable = False
        empty_text = "No learn progress for users."

    def render_details(self, record):
        url = reverse(
            "opportunity:user_learn_progress",
            kwargs={"org_slug": self.org_slug, "opp_id": record.opportunity.id, "pk": record.pk},
        )
        return mark_safe(f'<a href="{url}">View Details</a>')


def show_warning(record):
    if record.status not in (VisitValidationStatus.approved, VisitValidationStatus.rejected):
        if record.flagged:
            return "table-warning"
    return ""


class UserVisitReviewFilter(FilterSet):
    review_status = ChoiceFilter(choices=VisitReviewStatus.choices, empty_label="All Reviews")
    user = ModelChoiceFilter(queryset=User.objects.none(), empty_label="All Users", to_field_name="username")
    visit_date = DateRangeFilter()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.filters["user"].queryset = User.objects.filter(id__in=self.queryset.values_list("user_id", flat=True))
        self.filters["user"].field.label_from_instance = lambda obj: obj.name

        self.form.helper = FormHelper()
        self.form.helper.disable_csrf = True
        self.form.helper.form_class = "form-inline"
        self.form.helper.layout = Layout(
            Row(
                Column("review_status", css_class="col-md-3"),
                Column("user", css_class="col-md-3"),
                Column("visit_date", css_class="col-md-3"),
            )
        )
        for field_name in self.form.fields.keys():
            self.form.fields[field_name].widget.attrs.update({"@change": "$refs.reviewFilterForm.submit()"})

    class Meta:
        model = UserVisit
        fields = ["review_status", "user", "visit_date"]


class UserVisitFilter(UserVisitReviewFilter):
    status = ChoiceFilter(choices=VisitValidationStatus.choices, empty_label="All Visits")

    def __init__(self, *args, **kwargs):
        managed_opportunity = kwargs.pop("managed_opportunity", False)
        super().__init__(*args, **kwargs)
        fields = ["status"]
        if managed_opportunity:
            fields.append("review_status")
        self.form.helper.layout = Layout(Row(*[Column(field, css_class="col-md-3") for field in fields]))
        for field in fields:
            self.form.fields[field].widget.attrs.update({"@change": "$refs.visitFilterForm.submit()"})

    class Meta:
        model = UserVisit
        fields = ["status", "review_status"]


class UserVisitTable(OrgContextTable):
    # export only columns
    visit_id = columns.Column("Visit ID", accessor="xform_id", visible=False)
    username = columns.Column("Username", accessor="user__username", visible=False)
    form_json = columns.Column("Form JSON", accessor="form_json", visible=False)
    visit_date_export = columns.DateTimeColumn(
        verbose_name="Visit date", accessor="visit_date", format="c", visible=False
    )
    reason = columns.Column("Rejected Reason", accessor="reason", visible=False)
    justification = columns.Column("Justification", accessor="justification", visible=False)
    duration = columns.Column("Duration", accessor="duration", visible=False)
    entity_id = columns.Column("Entity ID", accessor="entity_id", visible=False)

    deliver_unit = columns.Column("Unit Name", accessor="deliver_unit__name")
    entity_name = columns.Column("Entity Name", accessor="entity_name")
    flag_reason = columns.Column("Flags", accessor="flag_reason", empty_values=({}, None))
    details = columns.Column(verbose_name="", empty_values=())

    def render_details(self, record):
        url = reverse(
            "opportunity:visit_verification",
            kwargs={"org_slug": self.org_slug, "pk": record.pk},
        )
        return mark_safe(f'<a class="btn btn-sm btn-primary" href="{url}">Review</a>')

    def render_flag_reason(self, value):
        short = [flag[1] for flag in value.get("flags")]
        return ", ".join(short)

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
        )
        empty_text = "No forms."
        orderable = False
        row_attrs = {"class": show_warning}
        template_name = "django_tables2/bootstrap5.html"


class OpportunityPaymentTable(OrgContextTable):
    display_name = columns.Column(verbose_name="Name")
    username = columns.Column(accessor="user__username", visible=False)
    view_payments = columns.Column(verbose_name="", empty_values=())

    def render_view_payments(self, record):
        url = reverse(
            "opportunity:worker_list",
            kwargs={"org_slug": self.org_slug, "opp_id": record.opportunity.id},
        )
        return mark_safe(f'<a href="{url}?active_tab=payments">View Details</a>')

    class Meta:
        model = OpportunityAccess
        fields = ("display_name", "username", "payment_accrued", "total_paid", "total_confirmed_paid")
        orderable = False
        empty_text = "No user have payments accrued yet."


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

    def render_view_profile(self, record):
        if not getattr(record.opportunity_access, "accepted", False):
            resend_invite_url = reverse(
                "opportunity:resend_user_invite", args=(self.org_slug, record.opportunity.id, record.id)
            )
            urls = [resend_invite_url]
            buttons = [
                """<button title="Resend invitation"
                hx-post="{}" hx-target="#modalBodyContent" hx-trigger="click"
                hx-on::after-request="handleResendInviteResponse(event)"
                class="btn btn-sm btn-success">Resend</button>"""
            ]
            if record.status == UserInviteStatus.not_found:
                invite_delete_url = reverse(
                    "opportunity:user_invite_delete", args=(self.org_slug, record.opportunity.id, record.id)
                )
                urls.append(invite_delete_url)
                buttons.append(
                    """<button title="Delete invitation"
                            hx-post="{}" hx-swap="none"
                            hx-confirm="Please confirm to delete the User Invite."
                            class="btn btn-sm btn-danger" type="button"><i class="bi bi-trash"></i></button>"""
                )
            button_html = f"""<div class="d-flex gap-1">{"".join(buttons)}</div>"""
            return format_html(button_html, *urls)
        url = reverse(
            "opportunity:user_profile",
            kwargs={"org_slug": self.org_slug, "opp_id": record.opportunity.id, "pk": record.opportunity_access_id},
        )
        return format_html('<a class="btn btn-primary btn-sm" href="{}">View Profile</a>', url)

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
    completed = SumColumn("Delivered")
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
            "completed",
            "pending",
            "approved",
            "rejected",
            "over_limit",
            "incomplete",
        )

    def render_details(self, record):
        url = reverse(
            "opportunity:user_visits_list",
            kwargs={"org_slug": self.org_slug, "opp_id": record.opportunity.id, "pk": record.pk},
        )
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
    revoke_suspension = columns.LinkColumn(
        "opportunity:revoke_user_suspension",
        verbose_name="",
        text="Revoke",
        args=[utils.A("opportunity__organization__slug"), utils.A("opportunity__id"), utils.A("pk")],
    )

    class Meta:
        model = OpportunityAccess
        fields = ("display_name", "suspension_date", "suspension_reason")
        orderable = False
        empty_text = "No suspended users."

    def render_suspension_date(self, record, value):
        return date_with_time_popup(self, value)

    def render_revoke_suspension(self, record, value):
        revoke_url = reverse(
            "opportunity:revoke_user_suspension",
            args=(record.opportunity.organization.slug, record.opportunity.id, record.pk),
        )
        page_url = reverse(
            "opportunity:suspended_users_list", args=(record.opportunity.organization.slug, record.opportunity_id)
        )
        return format_html('<a class="btn btn-success" href="{}?next={}">Revoke</a>', revoke_url, page_url)


class CatchmentAreaTable(tables.Table):
    username = columns.Column(accessor="opportunity_access__user__username", verbose_name="Username")
    name_of_user = columns.Column(accessor="opportunity_access__user__name", verbose_name="Name")
    phone_number = columns.Column(accessor="opportunity_access__user__phone_number", verbose_name="Phone Number")
    name = columns.Column(verbose_name="Area name")
    active = columns.Column(verbose_name="Active")
    latitude = columns.Column(verbose_name="Latitude")
    longitude = columns.Column(verbose_name="Longitude")
    radius = columns.Column(verbose_name="Radius")
    site_code = columns.Column(verbose_name="Site code")

    def render_active(self, value):
        return "Yes" if value else "No"

    class Meta:
        model = CatchmentArea
        fields = (
            "username",
            "site_code",
            "name",
            "name_of_user",
            "phone_number",
            "active",
            "latitude",
            "longitude",
            "radius",
        )
        orderable = False
        sequence = (
            "username",
            "name_of_user",
            "phone_number",
            "name",
            "site_code",
            "active",
            "latitude",
            "longitude",
            "radius",
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
        )
        empty_text = "No visits submitted for review."
        template_name = "django_tables2/bootstrap5.html"

    def render_user_visit(self, record):
        url = reverse(
            "opportunity:visit_verification",
            kwargs={"org_slug": self.org_slug, "pk": record.pk},
        )
        return mark_safe(f'<a href="{url}">View</a>')


class PaymentReportTable(tables.Table):
    payment_unit = columns.Column(verbose_name="Payment Unit")
    approved = SumColumn(verbose_name="Approved Units")
    user_payment_accrued = SumColumn(verbose_name="User Payment Accrued")
    nm_payment_accrued = SumColumn(verbose_name="Network Manager Payment Accrued")

    class Meta:
        orderable = False


class PaymentInvoiceTable(OpportunityContextTable):
    payment_status = columns.Column(verbose_name="Payment Status", accessor="payment", empty_values=())
    payment_date = columns.Column(verbose_name="Payment Date", accessor="payment", empty_values=(None))
    actions = tables.Column(empty_values=(), orderable=False, verbose_name="Pay")

    class Meta:
        model = PaymentInvoice
        orderable = False
        fields = ("amount", "date", "invoice_number", "service_delivery")
        sequence = (
            "amount",
            "date",
            "invoice_number",
            "payment_status",
            "payment_date",
            "service_delivery",
            "actions",
        )
        empty_text = "No Payment Invoices"

    def __init__(self, *args, **kwargs):
        self.csrf_token = kwargs.pop("csrf_token")
        super().__init__(*args, **kwargs)

    def render_payment_status(self, value):
        if value is not None:
            return "Paid"
        return "Pending"

    def render_payment_date(self, value):
        if value is not None:
            return value.date_paid
        return

    def render_actions(self, record):
        invoice_approve_url = reverse("opportunity:invoice_approve", args=[self.org_slug, self.opp_id])
        template_string = f"""
            <form method="POST" action="{ invoice_approve_url  }">
                <input type="hidden" name="csrfmiddlewaretoken" value="{ self.csrf_token }">
                <input type="hidden" name="pk" value="{ record.pk }">
                <button type="submit" class="button button-md outline-style" {'disabled' if getattr(record, 'payment', None) else ''}>Pay</button>
            </form>
        """
        return mark_safe(template_string)


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


def header_with_tooltip(label, tooltip_text):
    return mark_safe(
        f"""
        <span x-data x-tooltip.raw="{tooltip_text}">
            {label}
        </span>
        """
    )


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
                            <i class="fa-light fa-file-dashed-line"
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
                            <i class="invisible fa-light fa-file-dashed-line"></i>
                        </span>
                    {% endif %}
                </div>
            """,
    )

    status = tables.Column(verbose_name="Status", accessor="status", orderable=True)

    program = tables.Column(attrs=TEXT_CENTER_ATTR)
    start_date = DMYTColumn(attrs=TEXT_CENTER_ATTR)
    end_date = DMYTColumn(attrs=TEXT_CENTER_ATTR)

    class Meta:
        sequence = (
            "index",
            "opportunity",
            "entity_type",
            "status",
            "program",
            "start_date",
            "end_date",
        )

    def render_status(self, value):
        if value == 0:
            badge_class = "badge badge-sm bg-green-600/20 text-green-600"
            text = "Active"
        elif value == 1:
            badge_class = "badge badge-sm bg-orange-600/20 text-orange-600"
            text = "Ended"
        else:
            badge_class = "badge badge-sm bg-slate-100 text-slate-400"
            text = "Inactive"

        return format_html(
            '<div class="flex justify-start text-sm font-normal truncate text-brand-deep-purple overflow-clip overflow-ellipsis">'
            '  <span class="{}">{}</span>'
            "</div>",
            badge_class,
            text,
        )

    def format_date(self, date):
        return date.strftime("%d-%b-%Y") if date else "--"

    def _render_div(self, value, extra_classes=""):
        base_classes = "flex text-sm font-normal truncate text-brand-deep-purple " "overflow-clip overflow-ellipsis"
        all_classes = f"{base_classes} {extra_classes}".strip()
        return format_html('<div class="{}">{}</div>', all_classes, value)

    def render_opportunity(self, value, record):
        url = reverse("opportunity:detail", args=(self.org_slug, record.id))
        value = format_html('<a href="{}">{}</a>', url, value)
        return self._render_div(value, extra_classes="justify-start text-wrap")

    def render_program(self, value):
        return self._render_div(value if value else "--", extra_classes="justify-start")

    def render_worker_list_url_column(self, value, opp_id, active_tab="workers", sort=None):
        url = reverse("opportunity:worker_list", args=(self.org_slug, opp_id))
        url = f"{url}?active_tab={active_tab}"

        if sort:
            url += "&" + sort
        value = format_html('<a href="{}">{}</a>', url, value)
        return self._render_div(value, extra_classes=self.stats_style)


class OpportunityTable(BaseOpportunityList):
    col_attrs = merge_attrs(TEXT_CENTER_ATTR, STOP_CLICK_PROPAGATION_ATTR)

    pending_invites = tables.Column(
        verbose_name=header_with_tooltip(
            "Pending Invites", "Workers not yet clicked on invite link or started learning in app"
        ),
        attrs=col_attrs,
    )
    inactive_workers = tables.Column(
        verbose_name=header_with_tooltip("Inactive Workers", "Did not submit a Learn or Deliver form in 3 day"),
        attrs=col_attrs,
    )
    pending_approvals = tables.Column(
        verbose_name=header_with_tooltip(
            "Pending Approvals", "Deliveries that are flagged and require NM or PM approval"
        ),
        attrs=col_attrs,
    )
    payments_due = tables.Column(
        verbose_name=header_with_tooltip("Payments Due", "Worker payments accrued minus the amount paid"),
        attrs=col_attrs,
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
        return self.render_worker_list_url_column(value=value, opp_id=record.id)

    def render_inactive_workers(self, value, record):
        return self.render_worker_list_url_column(value=value, opp_id=record.id, sort="sort=last_active")

    def render_pending_approvals(self, value, record):
        return self.render_worker_list_url_column(
            value=value, opp_id=record.id, active_tab="delivery", sort="sort=-pending"
        )

    def render_payments_due(self, value, record):
        if value is None:
            value = 0

        value = f"{record.currency} {intcomma(value)}"
        return self.render_worker_list_url_column(
            value=value, opp_id=record.id, active_tab="payments", sort="sort=-total_paid"
        )

    def render_actions(self, record):
        actions = [
            {
                "title": "View Opportunity",
                "url": reverse("opportunity:detail", args=[self.org_slug, record.id]),
            },
            {
                "title": "View Workers",
                "url": reverse("opportunity:worker_list", args=[self.org_slug, record.id]),
            },
        ]

        if record.managed:
            actions.append(
                {
                    "title": "View Invoices",
                    "url": reverse("opportunity:invoice_list", args=[self.org_slug, record.id]),
                }
            )

        html = render_to_string(
            "tailwind/components/dropdowns/text_button_dropdown.html",
            context={
                "text": "...",
                "list": actions,
                "styles": "text-sm",
            },
        )
        return mark_safe(html)


class ProgramManagerOpportunityTable(BaseOpportunityList):

    active_workers = tables.Column(
        verbose_name=header_with_tooltip(
            "Active Workers", "Worker delivered a Learn or Deliver form in the last 3 days"
        ),
        attrs=TEXT_CENTER_ATTR,
    )
    total_deliveries = tables.Column(
        verbose_name=header_with_tooltip("Total Deliveries", "Payment units completed"), attrs=TEXT_CENTER_ATTR
    )
    verified_deliveries = tables.Column(
        verbose_name=header_with_tooltip("Verified Deliveries", "Payment units fully approved by PM and NM"),
        attrs=TEXT_CENTER_ATTR,
    )
    worker_earnings = tables.Column(
        verbose_name=header_with_tooltip("Worker Earnings", "Total payment accrued to worker"),
        accessor="total_accrued",
        attrs=TEXT_CENTER_ATTR,
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
        return self.render_worker_list_url_column(value=value, opp_id=record.id)

    def render_total_deliveries(self, value, record):
        return self.render_worker_list_url_column(
            value=value, opp_id=record.id, active_tab="delivery", sort="sort=-delivered"
        )

    def render_verified_deliveries(self, value, record):
        return self.render_worker_list_url_column(
            value=value, opp_id=record.id, active_tab="delivery", sort="sort=-approved"
        )

    def render_worker_earnings(self, value, record):
        url = reverse("opportunity:worker_list", args=(self.org_slug, record.id))
        url += "?active_tab=payments&sort=-payment_accrued"
        value = f"{record.currency} {intcomma(value)}"
        value = format_html('<a href="{}">{}</a>', url, value)
        return self._render_div(value, extra_classes=self.stats_style)

    def render_opportunity(self, record):
        url = reverse("opportunity:detail", args=(self.org_slug, record.id))
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

    def render_actions(self, record):
        actions = [
            {
                "title": "View Opportunity",
                "url": reverse("opportunity:detail", args=[self.org_slug, record.id]),
            },
            {
                "title": "View Workers",
                "url": reverse("opportunity:worker_list", args=[self.org_slug, record.id]),
            },
        ]

        if record.managed:
            actions.append(
                {
                    "title": "View Invoices",
                    "url": reverse("opportunity:invoice_list", args=[self.org_slug, record.id]),
                }
            )

        html = render_to_string(
            "tailwind/components/dropdowns/text_button_dropdown.html",
            context={
                "text": "...",
                "list": actions,
                "styles": "text-sm",
            },
        )
        return mark_safe(html)


class UserVisitVerificationTable(tables.Table):
    date_time = columns.DateTimeColumn(verbose_name="Date", accessor="visit_date", format="d M, Y H:i")
    entity_name = columns.Column(verbose_name="Entity Name")
    deliver_unit = columns.Column(verbose_name="Deliver Unit", accessor="deliver_unit__name")
    payment_unit = columns.Column(verbose_name="Payment Unit", accessor="completed_work__payment_unit__name")
    flags = columns.TemplateColumn(
        verbose_name="Flags",
        orderable=False,
        template_code="""
            <div class="flex relative justify-start text-sm text-brand-deep-purple font-normal w-72">
                {% if record %}
                    {% if record.status == 'over_limit' %}
                    <span class="badge badge-sm negative-light mx-1">{{ record.get_status_display|lower }}</span>
                    {% endif %}
                {% endif %}
                {% if value %}
                    {% for flag in value|slice:":2" %}
                        {% if flag == "duplicate"%}
                        <span class="badge badge-sm warning-light mx-1">
                        {% else %}
                        <span class="badge badge-sm primary-light mx-1">
                        {% endif %}
                            {{ flag }}
                        </span>
                    {% endfor %}
                    {% if value|length > 2 %}
                    {% include "tailwind/components/badges/badge_sm_dropdown.html" with title='All Flags' list=value %}
                    {% endif %}
                {% endif %}
            </div>
            """,
    )
    last_activity = columns.DateColumn(verbose_name="Last Activity", accessor="status_modified_date", format="d M, Y")
    icons = columns.Column(verbose_name="", empty_values=("",), orderable=False)

    class Meta:
        model = UserVisit
        sequence = (
            "date_time",
            "entity_name",
            "deliver_unit",
            "payment_unit",
            "flags",
            "last_activity",
            "icons",
        )
        fields = []
        empty_text = "No Visits for this filter."

    def __init__(self, *args, **kwargs):
        organization = kwargs.pop("organization", None)
        super().__init__(*args, **kwargs)
        self.use_view_url = True
        self.attrs = {"x-data": "{selectedRow: null}"}
        self.row_attrs = {
            "hx-get": lambda record: reverse(
                "opportunity:user_visit_details",
                args=[organization.slug, record.opportunity_id, record.pk],
            ),
            "hx-trigger": "click",
            "hx-indicator": "#visit-loading-indicator",
            "hx-target": "#visit-details",
            "hx-params": "none",
            "hx-swap": "innerHTML",
            "@click": lambda record: f"selectedRow = {record.id}",
            ":class": lambda record: f"selectedRow == {record.id} && 'active'",
        }

    def render_icons(self, record):
        status_meta = {
            # Review Status Pending, Visit Status Approved
            "approved_pending_review": {"icon": "fa-solid fa-circle-check text-slate-300/50",
                                        "tooltip": "Manually approved by NM"},
            VisitValidationStatus.approved: {"icon": "fa-solid fa-circle-check", "tooltip": "Auto-approved"},
            VisitValidationStatus.rejected: {"icon": "fa-light fa-ban"},
            VisitValidationStatus.pending: {"icon": "fa-light fa-flag-swallowtail"},
            VisitValidationStatus.duplicate: {"icon": "fa-light fa-clone"},
            VisitValidationStatus.trial: {"icon": "fa-light fa-marker"},
            VisitValidationStatus.over_limit: {"icon": "fa-light fa-marker"},
            VisitReviewStatus.disagree: {"icon": "fa-light fa-thumbs-down", "tooltip": "Disagreed by PM"},
            VisitReviewStatus.agree: {"icon": "fa-light fa-thumbs-up", "tooltip": "Agreed by PM"},
            # Review Status Pending (custom name, original choice clashes with Visit Pending)
            "pending_review": {"icon": "fa-light fa-timer", "tooltip": "Pending Review by PM"},
        }

        if record.status in (VisitValidationStatus.pending, VisitValidationStatus.duplicate):
            meta = status_meta.get(record.status)
            icon_class = meta["icon"]
            tooltip = meta.get("tooltip")

            icon_html = f'<i class="{icon_class} text-brand-deep-purple ml-4"></i>'

            if tooltip:
                icon_html = header_with_tooltip(icon_html, tooltip)

            return format_html(
                '<div class=" {} text-end text-brand-deep-purple text-lg">{}</div>',
                "justify-end",
                mark_safe(icon_html),
            )

        status = []
        if record.opportunity.managed and record.review_status and record.review_created_on:
            if record.review_status == VisitReviewStatus.pending.value:
                status.append("pending_review")
            else:
                status.append(record.review_status)
        if record.status in VisitValidationStatus:
            if (
                record.review_status != VisitReviewStatus.agree.value
                and record.review_created_on
                and record.status == VisitValidationStatus.approved
            ):
                status.append("approved_pending_review")
            else:
                status.append(record.status)

        icons_html = ""
        for status in status:
            meta = status_meta.get(status)
            icon_class = icon_class = meta["icon"]
            if icon_class:
                tooltip = meta.get("tooltip")
                icon_html = f'<i class="{icon_class} text-brand-deep-purple ml-4"></i>'
                if tooltip:
                    icon_html = header_with_tooltip(icon_html, tooltip)
                icons_html += icon_html

        justify_class = "justify-end" if len(status) == 1 else "justify-between"

        return format_html(
            '<div class=" {} text-end text-brand-deep-purple text-lg">{}</div>',
            justify_class,
            mark_safe(icons_html),
        )


class UserInfoColumn(tables.Column):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("orderable", True)
        kwargs.setdefault("verbose_name", "Name")
        kwargs.setdefault("order_by", "user__name")
        super().__init__(*args, **kwargs)

    def render(self, value, record):
        if not record.accepted:
            return "-"

        return format_html(
            """
            <div class="flex flex-col items-start w-40">
                <p class="text-sm text-slate-900">{}</p>
                <p class="text-xs text-slate-400">{}</p>
            </div>
            """,
            value.name,
            value.username,
        )


class SuspendedIndicatorColumn(tables.Column):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("orderable", False)
        kwargs.setdefault(
            "verbose_name", mark_safe('<div class="w-[40px]"><div class="w-4 h-2 bg-black rounded"></div></div>')
        )
        super().__init__(*args, **kwargs)

    def render(self, value):
        color_class = "negative-dark" if value else "positive-dark"
        return format_html('<div class="w-10"><div class="w-4 h-2 rounded {}"></div></div>', color_class)


class WorkerStatusTable(tables.Table):
    index = IndexColumn()
    user = UserInfoColumn()
    suspended = SuspendedIndicatorColumn()
    invited_date = DMYTColumn()
    last_active = DMYTColumn(verbose_name=header_with_tooltip("Last Active", "Submitted a Learn or Deliver form"))
    started_learn = DMYTColumn(
        verbose_name=header_with_tooltip("Started Learn", "Started download of the Learn app"),
        accessor="date_learn_started",
    )
    completed_learn = DMYTColumn(
        verbose_name=header_with_tooltip("Completed Learn", "Completed all Learn modules except assessment")
    )
    days_to_complete_learn = DurationColumn(verbose_name=header_with_tooltip("Time to Complete Learning",
                                                                             "Difference between Completed Learn and Started Learn"))
    first_delivery = DMYTColumn(
        verbose_name=header_with_tooltip("First Delivery", "Time stamp of when the first learn form was delivered"))
    days_to_start_delivery = DurationColumn(verbose_name=header_with_tooltip("Time to Start Deliver",
                                                                             "Time it took to deliver the first deliver form after invitation"))

    def __init__(self, *args, **kwargs):
        self.use_view_url = True
        super().__init__(*args, **kwargs)

    class Meta:
        order_by = ("-last_active",)


class WorkerPaymentsTable(tables.Table):
    index = IndexColumn()
    user = UserInfoColumn(footer="Total")
    suspended = SuspendedIndicatorColumn()
    last_active = DMYTColumn()
    payment_accrued = tables.Column(verbose_name="Accrued",
                                    footer=lambda table: intcomma(
                                        sum(x.payment_accrued or 0 for x in table.data)))
    total_paid = tables.Column(verbose_name="Total Paid",
                               footer=lambda table: intcomma(sum(x.total_paid or 0 for x in table.data)))
    last_paid = DMYTColumn()
    confirmed_paid = tables.Column(verbose_name="Confirm", accessor="total_confirmed_paid")

    def __init__(self, *args, **kwargs):
        self.use_view_url = True
        self.org_slug = kwargs.pop("org_slug", "")
        self.opp_id = kwargs.pop("opp_id", "")
        super().__init__(*args, **kwargs)

        try:
            currency = self.data[0].opportunity.currency
        except (IndexError, AttributeError):
            currency = ""

            # Update column headers with currency
        self.columns["payment_accrued"].column.verbose_name = f"Accrued ({currency})"
        self.columns["total_paid"].column.verbose_name = f"Total Paid ({currency})"
        self.columns["confirmed_paid"].column.verbose_name = f"Confirm ({currency})"

    class Meta:
        model = OpportunityAccess
        fields = ("user", "suspended", "payment_accrued", "confirmed_paid")
        sequence = (
            "index",
            "user",
            "suspended",
            "last_active",
            "payment_accrued",
            "total_paid",
            "last_paid",
            "confirmed_paid",
        )
        order_by = ("-last_active",)

    def render_last_paid(self, record, value):
        return render_to_string(
            "tailwind/components/worker_page/last_paid.html",
            {
                "record": record,
                "value": value.strftime("%d-%b-%Y") if value else "--",
                "org_slug": self.org_slug,
                "opp_id": self.opp_id,
            },
        )


class WorkerLearnTable(OrgContextTable):
    index = IndexColumn()
    user = UserInfoColumn()
    suspended = SuspendedIndicatorColumn()
    last_active = DMYTColumn()
    started_learning = DMYTColumn(accessor="date_learn_started", verbose_name="Started Learning")
    modules_completed = tables.TemplateColumn(
        accessor="modules_completed_percentage",
        template_code="""
                            {% include "tailwind/components/progressbar/simple-progressbar.html" with text=flag percentage=value|default:0 %}
                        """,
    )
    completed_learning = DMYTColumn(accessor="completed_learn", verbose_name="Completed Learning")
    assessment = tables.Column(accessor="assessment_status")

    attempts = tables.Column(accessor="assesment_count")
    learning_hours = DurationColumn()
    action = tables.TemplateColumn(
        verbose_name="",
        orderable=False,
        template_code="""
        """,
    )

    def __init__(self, *args, **kwargs):
        self.use_view_url = True
        self.opp_id = kwargs.pop("opp_id")
        super().__init__(*args, **kwargs)

    class Meta:
        model = OpportunityAccess
        fields = ("suspended", "user")
        sequence = (
            "index",
            "user",
            "suspended",
            "last_active",
            "started_learning",
            "modules_completed",
            "completed_learning",
            "assessment",
            "attempts",
            "learning_hours",
            "action",
        )

        order_by = ("-last_active",)

    def render_user(self, value, record):

        if not record.accepted:
            return "-"

        url = reverse("opportunity:worker_learn_progress", args=(self.org_slug, self.opp_id, record.id))
        return format_html(
            """
            <a href="{}" class="flex flex-col items-start w-40">
                <p class="text-sm text-slate-900">{}</p>
                <p class="text-xs text-slate-400">{}</p>
            </div>
            """,
            url,
            value.name,
            value.username,
        )

    def render_action(self, record):
        url = reverse("opportunity:worker_learn_progress", args=(self.org_slug, self.opp_id, record.id))
        return format_html(
            """ <div class="opacity-0 group-hover:opacity-100 transition-opacity duration-200 text-end">
                <a href="{url}"><i class="fa-solid fa-chevron-right text-brand-deep-purple"></i></a>
            </div>""",
            url=url,
        )


class TotalFlagCountsColumn(tables.Column):
    def __init__(self, *args, status=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.status = status

    def render_footer(self, bound_column, table):
        total = sum(bound_column.accessor.resolve(row) for row in table.data)

        url = reverse("opportunity:worker_flag_counts", args=[table.org_slug, table.opp_id])
        params = {"status": self.status}
        full_url = f"{url}?{urlencode(params)}"

        return render_to_string(
            "tailwind/components/worker_page/fetch_flag_counts.html",
            {
                "counts_url": full_url,
                "value": total,
                "status": self.status,
            },
        )


class TotalDeliveredColumn(tables.Column):
    def render_footer(self, bound_column, table):
        completed = sum(row.completed for row in table.data)
        incomplete = sum(row.incomplete for row in table.data)
        over_limit = sum(row.over_limit for row in table.data)

        rows = [
            {"label": "Completed", "value": completed},
            {"label": "Incomplete", "value": incomplete},
            {"label": "Over limit", "value": over_limit},
        ]
        return render_to_string(
            "tailwind/components/worker_page/deliver_column.html",
            {
                "value": completed,
                "rows": rows,
            },
        )


class WorkerDeliveryTable(OrgContextTable):
    use_view_url = True

    id = tables.Column(visible=False)
    index = IndexColumn()
    user = tables.Column(orderable=False, verbose_name="Name", footer="Total")
    suspended = SuspendedIndicatorColumn()
    last_active = DMYTColumn(empty_values=())
    payment_unit = tables.Column(orderable=False)
    delivery_progress = tables.Column(accessor="total_visits", empty_values=(), orderable=False)
    delivered = TotalDeliveredColumn(
        verbose_name=header_with_tooltip("Delivered", "Delivered number of payment units"),
        accessor="completed",
        order_by="total_completed",
    )
    pending = TotalFlagCountsColumn(
        verbose_name=header_with_tooltip("Pending", "Payment units with pending approvals with NM or PM"),
        status=CompletedWorkStatus.pending,
        order_by="total_pending",
    )
    approved = TotalFlagCountsColumn(
        verbose_name=header_with_tooltip(
            "Approved", "Payment units that are fully approved automatically or manually by NM and PM"
        ),
        status=CompletedWorkStatus.approved,
        order_by="total_approved",
    )
    rejected = TotalFlagCountsColumn(
        verbose_name=header_with_tooltip("Rejected", "Payment units that are rejected"),
        status=CompletedWorkStatus.rejected,
        order_by="total_rejected",
    )

    action = tables.TemplateColumn(
        verbose_name="",
        orderable=False,
        template_code="""

        """,
    )

    class Meta:
        model = OpportunityAccess
        fields = ("id", "suspended", "user")
        sequence = (
            "index",
            "user",
            "suspended",
            "last_active",
            "payment_unit",
            "delivery_progress",
            "delivered",
            "pending",
            "approved",
            "rejected",
            "action",
        )


    def __init__(self, *args, **kwargs):
        self.opp_id = kwargs.pop("opp_id")
        self.use_view_url = True
        super().__init__(*args, **kwargs)
        self._seen_users = set()


    def render_delivery_progress(self, record):
        current = record.completed
        total = record.total_visits

        if not total:
            return "-"

        percentage = round((current / total) * 100, 2)

        context = {
            "current": current,
            "percentage": percentage,
            "total": total,
            "number_style": True,
        }

        return render_to_string("tailwind/components/progressbar/simple-progressbar.html", context)


    def render_action(self, record):
        url = reverse("opportunity:user_visits_list", args=(self.org_slug, self.opp_id, record.id))
        template = """
            <div class="opacity-0 group-hover:opacity-100 transition-opacity duration-200 text-end">
                <a href="{}"><i class="fa-solid fa-chevron-right text-brand-deep-purple"></i></a>
            </div>
        """
        self.run_after_every_row(record)
        return format_html(template, url)

    def render_user(self, value, record):
        if record.id in self._seen_users:
            return ""


        url = reverse("opportunity:user_visits_list", args=(self.org_slug, self.opp_id, record.id))

        return format_html(
            """
                <a href="{}" class="w-40">
                    <p class="text-sm text-slate-900">{}</p>
                    <p class="text-xs text-slate-400">{}</p>
                </a>
            """,
            url,
            value.name,
            value.username,
        )

    def render_suspended(self, record, value):
        if record.id in self._seen_users:
            return ""
        return SuspendedIndicatorColumn().render(value)


    def run_after_every_row(self, record):
        self._seen_users.add(record.id)

    def render_index(self, value, record):
        page = getattr(self, "page", None)

        if not hasattr(self, "_row_counter"):
            seen_ids = set()
            unique_before_page = 0

            per_page = page.paginator.per_page
            page_start_index = (page.number - 1) * per_page

            for d in self.data[:page_start_index]:
                if d.id not in seen_ids:
                    seen_ids.add(d.id)
                    unique_before_page += 1

            self._row_counter = itertools.count(start=unique_before_page + 1)

        if record.id in self._seen_users:
            return ""

        return next(self._row_counter)

    def render_delivered(self, record, value):
        rows = [
            {"label": "Completed", "value": record.completed},
            {"label": "Incomplete", "value": record.incomplete},
            {"label": "Over limit", "value": record.over_limit},
        ]
        return render_to_string(
            "tailwind/components/worker_page/deliver_column.html",
            {
                "value": value,
                "rows": rows,
            },
        )

    def _render_flag_counts(self, record, value, status):
        url = reverse("opportunity:worker_flag_counts", args=[self.org_slug, self.opp_id])

        params = {
            "status": status,
            "payment_unit_id": record.payment_unit_id,
            "access_id": record.pk,
        }
        full_url = f"{url}?{urlencode(params)}"

        return render_to_string(
            "tailwind/components/worker_page/fetch_flag_counts.html",
            {
                "counts_url": full_url,
                "value": value,
                "status": status,
            },
        )

    def render_pending(self, record, value):
        return self._render_flag_counts(record, value, status=CompletedWorkStatus.pending)

    def render_approved(self, record, value):
        return self._render_flag_counts(record, value, status=CompletedWorkStatus.approved)

    def render_rejected(self, record, value):
        return self._render_flag_counts(record, value, status=CompletedWorkStatus.rejected)

    def render_last_active(self, record, value):
        if record.id in self._seen_users:
            return ""

        return DMYTColumn().render(value)


class WorkerLearnStatusTable(tables.Table):
    index = IndexColumn()
    module_name = tables.Column(accessor="module__name", orderable=False)
    date = DMYTColumn(verbose_name="Date Completed", accessor="date", orderable=False)
    duration = DurationColumn(accessor="duration", orderable=False)
    time = tables.Column(accessor="date", verbose_name="Time Completed", orderable=False)

    class Meta:
        sequence = ("index", "module_name", "date", "duration")


class LearnModuleTable(tables.Table):
    index = IndexColumn()

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
    deliver_units = tables.Column(verbose_name="Delivery Units")
    org_pay = tables.Column(verbose_name="Org pay", empty_values=())

    def __init__(self, *args, **kwargs):
        self.can_edit = kwargs.pop("can_edit", False)
        # For managed opp
        self.org_pay_per_visit = kwargs.pop("org_pay_per_visit", False)
        if not self.org_pay_per_visit:
            kwargs["exclude"] = "org_pay"
        super().__init__(*args, **kwargs)

    class Meta:
        model = PaymentUnit
        orderable = False
        fields = ("index", "name", "start_date", "end_date", "amount", "org_pay", "max_total", "max_daily", "deliver_units")
        empty_text = "No payment units for this opportunity."

    def render_org_pay(self, record):
        return self.org_pay_per_visit

    def render_deliver_units(self, record):
        deliver_units = record.deliver_units.all()
        count = deliver_units.count()

        if self.can_edit:
            edit_url = reverse("opportunity:edit_payment_unit", args=(self.org_slug, record.opportunity.id, record.id))
        else:
            edit_url = None

        context = {
            "count": count,
            "deliver_units": deliver_units,
            "edit_url": edit_url,
        }
        return render_to_string("tailwind/pages/opportunity_dashboard/extendable_payment_unit_row.html", context)
