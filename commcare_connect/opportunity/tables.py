import itertools
from urllib.parse import urlencode

import django_tables2 as tables
import waffle
from django.contrib.humanize.templatetags.humanize import intcomma
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django_tables2 import columns

from commcare_connect.flags.switch_names import INVOICE_REVIEW, UPDATES_TO_MARK_AS_PAID_WORKFLOW
from commcare_connect.opportunity.models import (
    AssignedTask,
    AssignedTaskStatus,
    CatchmentArea,
    CompletedWork,
    CompletedWorkStatus,
    DeliverUnit,
    InvoiceStatus,
    LearnModule,
    OpportunityAccess,
    PaymentInvoice,
    PaymentUnit,
    TaskType,
    UserInvite,
    UserInviteStatus,
    UserVisit,
    VisitReviewStatus,
    VisitValidationStatus,
)
from commcare_connect.utils.tables import (
    STOP_CLICK_PROPAGATION_ATTR,
    TEXT_CENTER_ATTR,
    DMYTColumn,
    DurationColumn,
    IndexColumn,
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


class PaymentInvoiceTable(OpportunityContextTable):
    amount = tables.Column(verbose_name="Amount")
    payment_status = columns.Column(verbose_name="Payment Status", accessor="payment", empty_values=())
    payment_date = columns.Column(verbose_name="Payment Date", accessor="payment", empty_values=(None))
    actions = tables.Column(empty_values=(), orderable=False, verbose_name="Actions")
    exchange_rate = tables.Column(orderable=False, empty_values=(None,), accessor="exchange_rate__rate")
    amount_usd = tables.Column(
        verbose_name=header_with_tooltip(
            "Amount (USD)",
            "Sum of USD amount over all line items in this invoice. Each line item is calculated as "
            "approved count × (payment unit amount ÷ exchange rate at time of approval) rounded to 2 "
            "decimals for each delivery",
        ),
    )
    status = tables.Column(verbose_name="Invoice Status")
    invoice_type = tables.Column(verbose_name="Invoice Type", accessor="service_delivery", empty_values=())
    last_status_modified_at = tables.Column(
        verbose_name=gettext_lazy("Invoice Last Updated Date"), accessor="last_status_modified_at"
    )

    class Meta:
        model = PaymentInvoice
        orderable = False
        fields = ("amount", "date", "invoice_number")
        sequence = (
            "amount",
            "amount_usd",
            "exchange_rate",
            "date",
            "invoice_number",
            "status",
            "payment_status",
            "last_status_modified_at",
            "payment_date",
            "invoice_type",
            "actions",
        )
        empty_text = "No Payment Invoices"
        row_attrs = {
            "class": lambda record, table: (
                "bg-yellow-100" if record.invoice_number == table.highlight_invoice_number else ""
            ),
        }

    def __init__(self, *args, **kwargs):
        self.csrf_token = kwargs.pop("csrf_token")
        self.opportunity = kwargs.pop("opportunity")
        self.highlight_invoice_number = kwargs.pop("highlight_invoice_number", None)
        self.is_pm = kwargs.pop("is_pm", False)
        super().__init__(*args, **kwargs)
        self.base_columns["amount"].verbose_name = f"Amount ({self.opportunity.currency_code})"
        # These changes can be done at class level when this switch is fully rolled out and no longer needed.
        if waffle.switch_is_active(UPDATES_TO_MARK_AS_PAID_WORKFLOW):
            self.columns["date"].column.verbose_name = _("Invoice Generation Date")
            self.columns.hide("payment_status")
        else:
            self.columns.hide("last_status_modified_at")

    def render_exchange_rate(self, value):
        if waffle.switch_is_active(UPDATES_TO_MARK_AS_PAID_WORKFLOW):
            return f"{round(value, 2)} {self.opportunity.currency_code} per USD"
        return value

    def render_payment_status(self, record, value):
        if record.status == InvoiceStatus.ARCHIVED:
            return "Archived"
        if value is not None:
            return "Paid"
        return "Pending"

    def render_payment_date(self, value):
        if value is not None:
            return value.date_paid
        return

    def render_invoice_type(self, record):
        if record.service_delivery:
            return _("Service Delivery")
        return _("Other")

    def render_status(self, record):
        if not waffle.switch_is_active(UPDATES_TO_MARK_AS_PAID_WORKFLOW):
            tooltips = {
                "Pending": _("Under review by Program Manager."),
                "Approved": _("Invoice Approved and Paid."),
                "Submitted": _("Submitted to Program Manager for Approval."),
                "Archived": _("Invoice Archived. No User Actions Allowed."),
            }
            status = record.get_status_display()
            return format_html('<span x-data x-tooltip.raw="{}">{}</span>', tooltips.get(status, ""), status)
        return record.get_status_display()

    def render_actions(self, record):
        review_button = ""
        if waffle.switch_is_active(INVOICE_REVIEW):
            invoice_review_url = reverse(
                "opportunity:invoice_review",
                args=[self.org_slug, str(self.opportunity.opportunity_id), str(record.payment_invoice_id)],
            )
            review_button = (
                f'<a href="{invoice_review_url}" '
                f'class="button button-md outline-style !inline-flex justify-center">'
                f'{_("Review")}</a>'
            )
        pay_button = ""
        if self.is_pm:
            required_status_for_pay = (
                InvoiceStatus.READY_TO_PAY
                if waffle.switch_is_active(UPDATES_TO_MARK_AS_PAID_WORKFLOW)
                else InvoiceStatus.PENDING_PM_REVIEW
            )
            if record.status == required_status_for_pay:
                invoice_pay_url = reverse(
                    "opportunity:invoice_pay", args=[self.org_slug, self.opportunity.opportunity_id]
                )
                disabled = "disabled" if getattr(record, "payment", None) else ""
                pay_button = f"""
                    <button
                        hx-post="{invoice_pay_url}"
                        hx-vals='{{"pk": "{record.payment_invoice_id}"}}'
                        hx-headers='{{"X-CSRFToken": "{self.csrf_token}"}}'
                        class="button button-md primary-dark"
                        {disabled}>
                        {_("Pay")}
                    </button>
                """  # noqa: E501
        return mark_safe(f'<div class="flex gap-2">{review_button}{pay_button}</div>')


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

    program = tables.Column()
    start_date = DMYTColumn()
    end_date = DMYTColumn()

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
        order_by = ("status", "-start_date", "end_date")
        empty_text = gettext_lazy("No Opportunities created yet.")

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
            '<div class="flex justify-start text-sm font-normal truncate text-brand-deep-purple overflow-clip overflow-ellipsis">'  # noqa: E501
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
        url = reverse("opportunity:detail", args=(self.org_slug, record.opportunity_id))
        value = format_html('<a href="{}">{}</a>', url, value)
        return self._render_div(value, extra_classes="justify-start text-wrap")

    def render_program(self, value):
        return self._render_div(value if value else "--", extra_classes="justify-start")

    def render_worker_list_url_column(self, value, opp_id, url_slug="worker_list", sort=None):
        url = reverse(f"opportunity:{url_slug}", args=(self.org_slug, opp_id))

        if sort:
            url += "?" + sort
        value = format_html('<a href="{}">{}</a>', url, value)
        return self._render_div(value, extra_classes=self.stats_style)


class OpportunityTable(BaseOpportunityList):
    col_attrs = merge_attrs(TEXT_CENTER_ATTR, STOP_CLICK_PROPAGATION_ATTR)

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

        if record.managed:
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


class ProgramManagerOpportunityTable(BaseOpportunityList):
    active_workers = tables.Column(
        verbose_name=header_with_tooltip(
            "Active Connect Workers", "Worker delivered a Learn or Deliver form in the last 3 days"
        ),
        attrs=TEXT_CENTER_ATTR,
        orderable=False,
    )
    total_deliveries = tables.Column(
        verbose_name=header_with_tooltip("Total Deliveries", "Payment units completed"),
        attrs=TEXT_CENTER_ATTR,
        orderable=False,
    )
    verified_deliveries = tables.Column(
        verbose_name=header_with_tooltip("Verified Deliveries", "Payment units fully approved by PM and NM"),
        attrs=TEXT_CENTER_ATTR,
        orderable=False,
    )
    worker_earnings = tables.Column(
        verbose_name=header_with_tooltip("Worker Earnings", "Total payment accrued to worker"),
        accessor="total_accrued",
        attrs=TEXT_CENTER_ATTR,
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

        if record.managed:
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


class WorkerVisitTable(tables.Table):
    date_time = DMYTColumn(verbose_name=gettext_lazy("Date"), accessor="visit_date")
    entity_name = columns.Column(verbose_name=gettext_lazy("Entity Name"))
    deliver_unit = columns.Column(verbose_name=gettext_lazy("Deliver Unit"), accessor="deliver_unit__name")
    payment_unit = columns.Column(
        verbose_name=gettext_lazy("Payment Unit"), accessor="completed_work__payment_unit__name"
    )
    last_activity = DMYTColumn(verbose_name=gettext_lazy("Last Activity"), accessor="status_modified_date")
    status = columns.Column(verbose_name=gettext_lazy("Status"), accessor="status")

    class Meta:
        model = UserVisit
        sequence = (
            "date_time",
            "entity_name",
            "deliver_unit",
            "payment_unit",
            "last_activity",
            "status",
        )
        fields = []
        empty_text = gettext_lazy("No Visits for this filter.")
        attrs = {
            "x-data": "{selectedRow: null}",
            "@change": "updateSelectAll()",
        }
        row_attrs = {
            "hx-get": lambda record, table: reverse(
                "opportunity:user_visit_details",
                args=[table.organization.slug, record.opportunity.opportunity_id, record.user_visit_id],
            ),
            "hx-trigger": "click",
            "hx-indicator": "#visit-loading-indicator",
            "hx-target": "#visit-details",
            "hx-params": "none",
            "hx-swap": "innerHTML",
            "@click": lambda record: f"selectedRow = {record.id}",
            ":class": lambda record: f"selectedRow == {record.id} && 'active'",
            "data-visit-id": lambda record: record.pk,
            "data-visit-status": lambda record: record.status,
        }

    def __init__(self, *args, **kwargs):
        self.organization = kwargs.pop("organization", None)
        self.is_opportunity_pm = kwargs.pop("is_opportunity_pm", False)
        super().__init__(*args, **kwargs)
        self.use_view_url = True

    def render_status(self, record):
        status = record.status
        if status == VisitValidationStatus.rejected:
            badge_classes = "bg-red-100 text-red-800"
        elif status == VisitValidationStatus.approved:
            badge_classes = "bg-green-100 text-green-800"
        else:
            badge_classes = "bg-gray-100 text-gray-800"
        return format_html(
            '<span class="inline-flex w-[80px] items-center justify-center px-3 py-1 rounded text-xs font-medium {}">'
            "{}</span>",
            badge_classes,
            record.get_status_display(),
        )


class UserVisitVerificationTable(WorkerVisitTable):
    flags = columns.TemplateColumn(
        verbose_name="Flags",
        orderable=False,
        template_code="""
            <div class="flex relative justify-start text-sm text-brand-deep-purple font-normal">
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
                    {% include "components/badges/badge_sm_dropdown.html" with title='All Flags' list=value %}
                    {% endif %}
                {% endif %}
            </div>
            """,
    )
    icons = columns.Column(verbose_name="", empty_values=("",), orderable=False)
    select = select_column(th_extra={"class": "checkbox ga-all-visit-checkbox"})

    class Meta(WorkerVisitTable.Meta):
        sequence = (
            "select",
            "date_time",
            "entity_name",
            "deliver_unit",
            "payment_unit",
            "flags",
            "last_activity",
            "icons",
        )
        exclude = ("status",)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.columns["select"].column.visible = not self.is_opportunity_pm

    def get_icons(self, statuses):
        status_meta = {
            # Review Status Pending, Visit Status Approved
            "approved_pending_review": {
                "icon": "fa-solid fa-circle-check text-slate-300/50",
                "tooltip": "Manually approved by NM",
            },
            VisitValidationStatus.approved: {"icon": "fa-solid fa-circle-check", "tooltip": "Auto-approved"},
            VisitValidationStatus.rejected: {"icon": "fa-solid fa-ban", "tooltip": "Rejected by NM"},
            VisitValidationStatus.pending: {
                "icon": "fa-solid fa-flag",
                "tooltip": "Waiting for NM Review",
            },
            VisitValidationStatus.duplicate: {"icon": "fa-solid fa-clone", "tooltip": "Duplicate Visit"},
            VisitValidationStatus.trial: {"icon": "fa-solid fa-marker", "tooltip": "Trail Visit"},
            VisitValidationStatus.over_limit: {"icon": "fa-solid fa-marker", "tooltip": "Daily limit exceeded"},
            VisitReviewStatus.disagree: {"icon": "fa-solid fa-thumbs-down", "tooltip": "Disagreed by PM"},
            VisitReviewStatus.agree: {"icon": "fa-solid fa-thumbs-up", "tooltip": "Agreed by PM"},
            # Review Status Pending (custom name, original choice clashes with Visit Pending)
            "pending_review": {"icon": "fa-solid fa-stopwatch", "tooltip": "Pending Review by PM"},
        }

        icons_html = []

        for status in statuses:
            meta = status_meta.get(status)
            icon_class = meta.get("icon")
            if icon_class:
                tooltip = meta.get("tooltip")
                icon_html = format_html('<i class="{} text-brand-deep-purple ml-4"></i>', icon_class)
                if tooltip:
                    icon_html = header_with_tooltip(icon_html, tooltip)
                icons_html.append(icon_html)

        justify_class = "justify-end" if len(statuses) == 1 else "justify-between"
        icons = "".join(icons_html)
        return format_html(
            '<div class="{} text-end text-brand-deep-purple text-lg">{}</div>', justify_class, mark_safe(icons)
        )

    def render_icons(self, record):
        if record.status in (VisitValidationStatus.pending, VisitValidationStatus.duplicate):
            return self.get_icons([record.status])

        status = []
        if record.opportunity.managed and record.review_status and record.review_created_on:
            if (
                record.review_status == VisitReviewStatus.pending.value
                and record.status == VisitValidationStatus.approved
            ):  # Show "pending_review" only if NM approved first
                status.append("pending_review")
            elif record.review_status in [VisitReviewStatus.agree, VisitReviewStatus.disagree]:
                status.append(record.review_status)

        if record.status in VisitValidationStatus:
            if record.review_created_on and record.status == VisitValidationStatus.approved:
                status.append("approved_pending_review")
            else:
                status.append(record.status)

        return self.get_icons(status)


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


class UserInviteInfoColumn(UserInfoColumn):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("order_by", "opportunity_access__user__name")
        kwargs.setdefault(
            "verbose_name",
            header_with_tooltip(
                label=_("Name"),
                tooltip_text=_("A blank value will be displayed if a worker does not have a PersonalID account"),
            ),
        )
        super().__init__(*args, **kwargs)

    def render(self, value, record):
        if value:
            return super().render(value, record.opportunity_access)
        return "—"


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
                '<span x-data x-tooltip.raw="{}">' '<i class="fa-solid fa-minus-square text-black-600"></i>' "</span>",
                _("User suspended"),
            )

        if record.status == UserInviteStatus.accepted:
            return format_html(
                '<span x-data x-tooltip.raw="{}">' '<i class="fa-solid fa-circle-check text-green-600"></i>' "</span>",
                _("Invite accepted"),
            )
        elif record.status in [UserInviteStatus.invited, UserInviteStatus.sms_delivered]:
            return format_html(
                '<span x-data x-tooltip.raw="{}">' '<i class="fa-regular fa-clock text-orange-600"></i>' "</span>",
                _("Invite pending"),
            )

        if record.status in [UserInviteStatus.not_found, UserInviteStatus.sms_not_delivered]:
            return format_html(
                '<span x-data x-tooltip.raw="{}">' '<i class="fa-solid fa-circle-xmark text-red-600"></i>' "</span>",
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


class WorkerStatusTable(tables.Table):
    select = tables.CheckBoxColumn(
        accessor="pk",
        attrs={
            "th__input": {
                "@click": "toggleSelectAll()",
                "x-model": "selectAll",
                "name": "select_all",
                "type": "checkbox",
                "class": "checkbox",
            },
            "td__input": {
                "x-model": "selected",
                "@click.stop": "",  # used to stop click propagation
                "name": "row_select",
                "type": "checkbox",
                "class": "checkbox",
                "value": lambda record: record.pk,
                "id": lambda record: f"row_checkbox_{record.pk}",
            },
        },
    )
    index = IndexColumn()
    user = UserInviteInfoColumn(
        accessor="opportunity_access__user",
        empty_values=(),
    )
    phone_number = tables.Column(accessor="phone_number", verbose_name="Phone Number")
    status = StatusIndicatorColumn(orderable=False)
    invited_date = DMYTColumn(accessor="notification_date", verbose_name=_("Invited Date"))
    last_active = DMYTColumn(
        verbose_name=header_with_tooltip("Last Active", "Submitted a Learn or Deliver form"),
        accessor="opportunity_access__last_active",
    )
    started_learn = DMYTColumn(
        verbose_name=header_with_tooltip("Started Learn", "Started download of the Learn app"),
        accessor="opportunity_access__date_learn_started",
    )
    completed_learn = DMYTColumn(
        verbose_name=header_with_tooltip("Completed Learn", "Completed all Learn modules except assessment"),
        accessor="opportunity_access__completed_learn_date",
    )
    days_to_complete_learn = DurationColumn(
        verbose_name=header_with_tooltip(
            "Time to Complete Learning", "Difference between Completed Learn and Started Learn"
        )
    )
    first_delivery = DMYTColumn(
        verbose_name=header_with_tooltip("First Delivery", "Time stamp of when the first learn form was delivered")
    )
    days_to_start_delivery = DurationColumn(
        verbose_name=header_with_tooltip(
            "Time to Start Deliver", "Time it took to deliver the first deliver form after invitation"
        )
    )

    def __init__(self, *args, **kwargs):
        self.use_view_url = False
        super().__init__(*args, **kwargs)

    class Meta:
        sequence = (
            "select",
            "index",
            "status",
            "user",
            "phone_number",
            "invited_date",
            "last_active",
            "started_learn",
            "completed_learn",
            "days_to_complete_learn",
            "first_delivery",
            "days_to_start_delivery",
        )
        order_by = ("-last_active",)
        attrs = {
            "@change": "updateSelectAll()",
        }


class WorkerPaymentsTable(tables.Table):
    index = IndexColumn()
    user = UserInfoColumn(footer="Total")
    suspended = SuspendedIndicatorColumn()
    last_active = DMYTColumn()
    payment_accrued = tables.Column(
        verbose_name="Accrued", footer=lambda table: intcomma(sum(x.payment_accrued or 0 for x in table.data))
    )
    total_paid = tables.Column(
        verbose_name="Total Paid",
        accessor="total_paid_d",
        footer=lambda table: intcomma(sum(x.total_paid or 0 for x in table.data)),
    )
    last_paid = DMYTColumn()
    confirmed_paid = tables.Column(verbose_name="Confirm", accessor="confirmed_paid_d")

    def __init__(self, *args, **kwargs):
        self.use_view_url = False
        self.org_slug = kwargs.pop("org_slug", "")
        self.opp_id = kwargs.pop("opp_id", "")
        super().__init__(*args, **kwargs)

        try:
            currency = self.data[0].opportunity.currency_code
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
            "components/worker_page/last_paid.html",
            {
                "record": record,
                "value": value.strftime("%d-%b-%Y") if value else "--",
                "org_slug": self.org_slug,
                "opp_id": self.opp_id,
            },
        )


class GroupedByWorkerMixin:
    """Mixin for tables that show multiple rows per worker, hiding worker-level
    columns on sub-rows. Subclasses must call `run_after_every_row(record)`
    in their last rendered column."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._seen_users = set()

    def _is_seen(self, record):
        return record.pk in self._seen_users

    def run_after_every_row(self, record):
        self._seen_users.add(record.pk)

    def _get_user_url(self, record):
        base_url = reverse("opportunity:user_visits_list", args=(self.org_slug, self.opp_id))
        return f"{base_url}?{urlencode({'user': record.user.user_id})}"

    def render_user(self, value, record):
        if self._is_seen(record):
            return ""
        return format_html(
            '<a href="{}" class="w-40">'
            '<p class="text-sm text-slate-900">{}</p>'
            '<p class="text-xs text-slate-400">{}</p>'
            "</a>",
            self._get_user_url(record),
            value.name,
            value.username,
        )

    def render_index(self, value, record):
        page = getattr(self, "page", None)

        if not hasattr(self, "_row_counter"):
            unique_before_page = 0

            if page:
                per_page = page.paginator.per_page
                page_start_index = (page.number - 1) * per_page
                seen_ids = set()

                for d in self.data[:page_start_index]:
                    if d.pk not in seen_ids:
                        seen_ids.add(d.pk)
                        unique_before_page += 1

            self._row_counter = itertools.count(start=unique_before_page + 1)

        if self._is_seen(record):
            return ""

        return next(self._row_counter)


class WorkerTasksTable(GroupedByWorkerMixin, OrgContextTable):
    index = IndexColumn()
    worker_status = StatusIndicatorColumn(verbose_name=_("Status"), empty_values=())
    user = tables.Column(verbose_name=_("Name"), orderable=True, order_by="user__name")
    task_name = tables.Column(verbose_name=_("Task Name"), empty_values=())
    date_assigned = DMYTColumn(verbose_name=_("Date Assigned"))
    due_date = DMYTColumn(verbose_name=_("Due Date"), accessor="task_due_date")
    task_status = TaskStatusColumn(verbose_name=_("Task Status"), empty_values=())

    class Meta:
        fields = ()
        orderable = False
        sequence = (
            "index",
            "worker_status",
            "user",
            "task_name",
            "date_assigned",
            "due_date",
            "task_status",
        )
        row_attrs = {"class": "group"}
        empty_text = gettext_lazy("No workers have accepted this opportunity yet.")

    def __init__(self, *args, **kwargs):
        self.opp_id = kwargs.pop("opp_id")
        super().__init__(*args, **kwargs)

    def render_worker_status(self, record, value):
        if self._is_seen(record):
            return ""
        return StatusIndicatorColumn.render(self.columns["worker_status"].column, record)

    def render_task_name(self, value):
        return value

    def render_task_status(self, value, record):
        self.run_after_every_row(record)
        return TaskStatusColumn.render(self.columns["task_status"].column, value)


class WorkerLearnTable(OrgContextTable):
    index = IndexColumn()
    user = UserInfoColumn()
    suspended = SuspendedIndicatorColumn()
    last_active = DMYTColumn()
    started_learning = DMYTColumn(accessor="date_learn_started", verbose_name="Started Learning")
    modules_completed = tables.TemplateColumn(
        accessor="modules_completed_percentage",
        template_code="""
            {% include "components/progressbar/simple-progressbar.html" with text=flag percentage=value|default:0 %}
        """,  # noqa: E501
    )
    completed_learning = DMYTColumn(accessor="completed_learn_date", verbose_name="Completed Learning")
    assessment = tables.Column(accessor="assessment_status_rank")

    attempts = tables.Column(accessor="assesment_count")
    learning_hours = DurationColumn()
    action = tables.TemplateColumn(
        verbose_name="",
        orderable=False,
        template_code="""
        """,
    )

    def __init__(self, *args, **kwargs):
        self.use_view_url = False
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
        row_attrs = {"class": "group"}

    def render_user(self, value, record):
        if not record.accepted:
            return "-"

        url = reverse(
            "opportunity:worker_learn_progress", args=(self.org_slug, self.opp_id, record.opportunity_access_id)
        )
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
        url = reverse(
            "opportunity:worker_learn_progress", args=(self.org_slug, self.opp_id, record.opportunity_access_id)
        )
        return format_html(
            """ <div class="opacity-0 group-hover:opacity-100 transition-opacity duration-200 text-end">
                <a href="{url}"><i class="fa-solid fa-chevron-right text-brand-deep-purple"></i></a>
            </div>""",
            url=url,
        )

    def render_assessment(self, value):
        status = "-"
        if value == 2:
            status = "Passed"
        elif value == 1:
            status = "Failed"

        return status


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
            "components/worker_page/fetch_flag_counts.html",
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
            "components/worker_page/deliver_column.html",
            {
                "value": completed,
                "rows": rows,
            },
        )


class WorkerDeliveryTable(GroupedByWorkerMixin, OrgContextTable):
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
        order_by = ("user.name", "-last_active")
        row_attrs = {"class": "group"}

    def __init__(self, *args, **kwargs):
        self.opp_id = kwargs.pop("opp_id")
        self.use_view_url = False
        super().__init__(*args, **kwargs)

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

        return render_to_string("components/progressbar/simple-progressbar.html", context)

    def render_action(self, record):
        base_url = reverse("opportunity:user_visits_list", args=(self.org_slug, self.opp_id))
        url = f"{base_url}?{urlencode({'user': record.user.user_id})}"
        template = """
            <div class="opacity-0 group-hover:opacity-100 transition-opacity duration-200 text-end">
                <a href="{}"><i class="fa-solid fa-chevron-right text-brand-deep-purple"></i></a>
            </div>
        """
        self.run_after_every_row(record)
        return format_html(template, url)

    def render_suspended(self, record, value):
        if self._is_seen(record):
            return ""
        return SuspendedIndicatorColumn().render(value)

    def render_delivered(self, record, value):
        rows = [
            {"label": "Completed", "value": record.completed},
            {"label": "Incomplete", "value": record.incomplete},
            {"label": "Over limit", "value": record.over_limit},
        ]
        return render_to_string(
            "components/worker_page/deliver_column.html",
            {
                "value": value,
                "rows": rows,
            },
        )

    def _render_flag_counts(self, record, value, status):
        url = reverse("opportunity:worker_flag_counts", args=[self.org_slug, self.opp_id])

        params = {
            "status": status,
            "payment_unit_id": record.payment_unit_payment_unit_id,
            "access_id": record.opportunity_access_id,
        }
        full_url = f"{url}?{urlencode(params)}"

        return render_to_string(
            "components/worker_page/fetch_flag_counts.html",
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
        if self._is_seen(record):
            return ""

        return DMYTColumn().render(value)


class WorkerLearnStatusTable(tables.Table):
    index = IndexColumn()
    module_name = tables.Column(accessor="module__name", orderable=False)
    duration = DurationColumn(accessor="duration", orderable=False)
    date = DMYTColumn(verbose_name="Date Completed", accessor="date", orderable=False)

    class Meta:
        sequence = ("index", "module_name", "duration", "date")


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
    deliver_units = tables.Column(verbose_name="Delivery Units")
    amount = tables.Column(verbose_name="Worker pay per delivery")
    org_amount = tables.Column(verbose_name="Org pay per delivery")

    def __init__(self, *args, **kwargs):
        self.can_edit = kwargs.pop("can_edit", False)
        if not kwargs.pop("is_program_manager", False):
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


class InvoiceLineItemsTable(tables.Table):
    month = tables.Column()
    payment_unit_name = tables.Column(verbose_name="Payment Unit")
    number_approved = tables.Column()
    amount_per_unit = tables.Column(
        verbose_name="Payment Unit Amount (local)",
    )
    total_amount_local = tables.Column(
        verbose_name="Total Amount (local)",
    )
    exchange_rate = tables.Column()
    total_amount_usd = tables.Column(
        verbose_name=header_with_tooltip(
            "Total Amount (USD)",
            "Approved count × (payment unit amount ÷ exchange rate at time of approval) "
            "rounded to 2 decimals for each delivery",
        ),
    )

    def __init__(self, currency, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if currency:
            self.columns["amount_per_unit"].column.verbose_name = f"Payment Unit Amount ({currency})"
            self.columns["total_amount_local"].column.verbose_name = f"Total Amount ({currency})"

    class Meta:
        orderable = False
        empty_text = "No invoice items found."
        attrs = {"class": "min-w-full rounded-lg shadow-md bg-white", "thead": {"class": "bg-gray-100"}}
        row_attrs = {"class": "even:bg-gray-50 text-gray-800 hover:bg-gray-100"}

    def render_month(self, value):
        return value.strftime("%B %Y")


class InvoiceDeliveriesTable(tables.Table):
    date_approved = DMYTColumn(verbose_name=_("Date Approved"), accessor="status_modified_date")
    opportunity = tables.Column(verbose_name=_("Opportunity"), accessor="payment_unit__opportunity__name")
    approved_count = tables.Column(verbose_name=_("Approved Deliveries"), accessor="saved_approved_count")
    payment_accrued = tables.Column(verbose_name=_("Payment Accrued"), accessor="saved_payment_accrued")
    payment_accrued_usd = tables.Column(verbose_name=_("Payment Accrued (USD)"), accessor="saved_payment_accrued_usd")
    entity_name = tables.Column(verbose_name=_("Beneficiary"), accessor="entity_name")
    date_created = DMYTColumn(verbose_name=_("Date of Delivery"), accessor="date_created")
    username = tables.Column(verbose_name=_("Worker"), accessor="opportunity_access__user__name")

    def __init__(self, currency, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.columns["payment_accrued"].column.verbose_name = f"Payment Accrued ({currency})"

    class Meta:
        model = CompletedWork
        fields = ("payment_unit",)
        sequence = (
            "payment_unit",
            "opportunity",
            "entity_name",
            "username",
            "date_created",
            "date_approved",
            "approved_count",
            "payment_accrued",
            "payment_accrued_usd",
        )


_task_select_td_extra = {
    "@change": "updateSelectAll()",
    # return None instead of False to skip the attribute.
    "disabled": lambda record: True if record.status == AssignedTaskStatus.COMPLETED else None,
}


class AssignedTaskListTable(OpportunityContextTable):
    select = select_column(td_extra=_task_select_td_extra)
    assigned_task_id = tables.Column(verbose_name=gettext_lazy("Task ID"), accessor="pk")
    connect_worker = tables.Column(verbose_name=gettext_lazy("Connect Worker"), accessor="opportunity_access__user")
    status = TaskStatusColumn(verbose_name=gettext_lazy("Status"), accessor="status")
    task_type = tables.Column(verbose_name=gettext_lazy("Task Type"), accessor="task_type__name")
    assigned_date = DMYTColumn(verbose_name=gettext_lazy("Assigned Date"), accessor="date_created")
    due_date = DMYTColumn(verbose_name=gettext_lazy("Due Date"), accessor="due_date")
    assigned_by = tables.Column(
        verbose_name=gettext_lazy("Assigned By"),
        accessor="assigned_by__name",
        empty_values=(None,),
        default=gettext_lazy("Deleted user"),
    )
    action = tables.TemplateColumn(
        verbose_name="",
        orderable=False,
        template_name="opportunity/assigned_task_edit_button.html",
    )

    class Meta:
        model = AssignedTask
        fields = ()
        sequence = (
            "select",
            "assigned_task_id",
            "connect_worker",
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

    def render_assigned_task_id(self, value):
        # TODO: CCCT-2184 - Link to Connect Worker page filtered to task view
        return format_html(
            '<a href="#" class="text-brand-indigo hover:underline">'
            '<span class="text-sm font-medium">#{}</span></a>',
            value,
        )

    def render_connect_worker(self, value):
        return format_html(
            '<div class="flex flex-col items-start">'
            '<p class="text-sm font-medium text-gray-700">{}</p>'
            '<p class="text-xs text-gray-500">{}</p>'
            "</div>",
            value.name,
            value.username,
        )


class WorkerCompletedTaskTable(tables.Table):
    use_view_url = True

    select = select_column(td_extra=_task_select_td_extra)
    task_type = tables.Column(verbose_name=_("Task Type"), accessor="task", orderable=False)
    assigned_by = tables.Column(
        verbose_name=_("Assigned By"),
        accessor="assigned_by__name",
        empty_values=(None,),
        default=gettext_lazy("Deleted user"),
    )
    assigned_date = DMYTColumn(verbose_name=_("Assigned Date"), accessor="date_created")
    due_date = DMYTColumn(verbose_name=_("Due Date"))
    status = TaskStatusColumn(verbose_name=_("Status"), accessor="status")

    class Meta:
        model = AssignedTask
        fields = ()
        sequence = ("select", "task_type", "assigned_by", "assigned_date", "due_date", "status")
        order_by = ("-assigned_date",)
        empty_text = gettext_lazy("No tasks have been assigned to this worker yet.")
        attrs = {"class": "w-full", "x-data": "{selectedRow: null}"}
        row_attrs = {
            "hx-get": lambda record, table: reverse(
                "opportunity:user_task_details",
                args=[
                    table.organization.slug,
                    record.opportunity_access.opportunity.opportunity_id,
                    record.assigned_task_id,
                ],
            ),
            "hx-trigger": "click",
            "hx-indicator": "#task-loading-indicator",
            "hx-target": "#task-details",
            "hx-params": "none",
            "hx-swap": "innerHTML",
            "@click": lambda record: f"selectedRow = {record.id}",
            ":class": lambda record: f"selectedRow == {record.id} && 'active'",
        }

    def __init__(self, *args, **kwargs):
        self.organization = kwargs.pop("organization", None)
        self.can_manage_tasks = kwargs.pop("can_manage_tasks", False)
        super().__init__(*args, **kwargs)
        if not self.can_manage_tasks:
            self.columns.hide("select")

    def render_task_type(self, value):
        return format_html("{} ({})", value.name, value.slug)


class TaskTable(OpportunityContextTable):
    index = IndexColumn()
    name = tables.Column(verbose_name=gettext_lazy("Task Type Name"))
    description = tables.Column(verbose_name=gettext_lazy("Description"))
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
        fields = ("index", "name", "description", "linked_task_unit", "archived", "actions")
        empty_text = gettext_lazy("No task types configured for this opportunity.")
