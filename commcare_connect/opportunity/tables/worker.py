"""Tables for the worker pages: the Connect Workers tabs and the per-worker visits and tasks lists."""

from urllib.parse import urlencode

import django_tables2 as tables
from django.contrib.humanize.templatetags.humanize import intcomma
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django_tables2 import columns

from commcare_connect.opportunity.models import (
    AssignedTask,
    CompletedWorkStatus,
    OpportunityAccess,
    UserVisit,
    VisitReviewStatus,
    VisitValidationStatus,
)
from commcare_connect.opportunity.tables import (
    BaseAssignedTaskTable,
    StatusIndicatorColumn,
    TaskStatusColumn,
    header_with_tooltip,
)
from commcare_connect.utils.tables import (
    DMYTColumn,
    DurationColumn,
    GroupedTable,
    IndexColumn,
    OrgContextTable,
    select_column,
)


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
            "hx-push-url": lambda record, table: (
                f"{reverse('opportunity:user_visits_list', args=[table.organization.slug, record.opportunity.opportunity_id])}"  # noqa
                f"?{urlencode({'user': record.user.user_id, 'visit_id': record.user_visit_id})}"
            ),
            "hx-trigger": "click",
            "hx-indicator": "#visit-loading-indicator",
            "hx-target": "#visit-details",
            "hx-params": "none",
            "hx-swap": "innerHTML",
            "@click": lambda record: f"selectedRow = {record.id}",
            ":class": lambda record, table: (
                f"(selectedRow == {record.id} || (selectedRow === null && "
                f"{str(str(record.user_visit_id) == table.highlighted_visit_id).lower()})) "
                f"? 'active' : ''"
            ),
            "data-visit-id": lambda record: record.pk,
            "data-visit-status": lambda record: record.status,
        }

    def __init__(self, *args, **kwargs):
        self.organization = kwargs.pop("organization", None)
        self.is_opportunity_pm = kwargs.pop("is_opportunity_pm", False)
        self.highlighted_visit_id = kwargs.pop("highlighted_visit_id", None)
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
        badge = format_html(
            '<span class="inline-flex w-[80px] items-center justify-center px-3 py-1 rounded text-xs font-medium {}">'
            "{}</span>",
            badge_classes,
            record.get_status_display(),
        )
        if status == VisitValidationStatus.over_limit:
            return header_with_tooltip(badge, "; ".join(record.get_over_limit_reasons_display()))
        return badge


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

    def get_icons(self, statuses, record):
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
            VisitValidationStatus.over_limit: {
                "icon": "fa-solid fa-marker",
                "tooltip": "; ".join(record.get_over_limit_reasons_display()),
            },
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
            return self.get_icons([record.status], record)

        status = []
        if record.review_status and record.review_created_on:
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

        return self.get_icons(status, record)


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
    status = StatusIndicatorColumn(orderable=False)
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
        fields = ("user", "payment_accrued", "confirmed_paid")
        sequence = (
            "index",
            "status",
            "user",
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


class WorkerWorkAreaTable(OrgContextTable):
    index = IndexColumn()
    user = UserInfoColumn()
    last_active = DMYTColumn(verbose_name=gettext_lazy("Last Active"))
    assigned_buildings = tables.Column(
        verbose_name=gettext_lazy("Assigned Buildings"),
        footer=lambda table: intcomma(sum(x.assigned_buildings or 0 for x in table.data)),
    )
    assigned_visits = tables.Column(
        verbose_name=gettext_lazy("Assigned Visits"),
        footer=lambda table: intcomma(sum(x.assigned_visits or 0 for x in table.data)),
    )
    assigned_work_areas = tables.Column(
        verbose_name=gettext_lazy("Assigned Work Areas"),
        footer=lambda table: intcomma(sum(x.assigned_work_areas or 0 for x in table.data)),
    )
    assigned_work_area_groups = tables.Column(
        verbose_name=gettext_lazy("Assigned Work Area Groups"),
        footer=lambda table: intcomma(sum(x.assigned_work_area_groups or 0 for x in table.data)),
    )
    visits_done = tables.Column(
        verbose_name=gettext_lazy("Visits Done"),
        footer=lambda table: intcomma(sum(x.visits_done or 0 for x in table.data)),
    )

    def __init__(self, *args, **kwargs):
        self.use_view_url = False
        super().__init__(*args, **kwargs)

    class Meta:
        model = OpportunityAccess
        fields = ("user",)
        sequence = (
            "index",
            "user",
            "last_active",
            "assigned_buildings",
            "assigned_visits",
            "assigned_work_areas",
            "assigned_work_area_groups",
            "visits_done",
        )
        order_by = ("-last_active",)


class WorkerTasksTable(GroupedTable, OrgContextTable):
    index = IndexColumn()
    worker_status = StatusIndicatorColumn(verbose_name=_("Status"), empty_values=())
    user = UserInfoColumn(verbose_name=_("Name"), orderable=True, order_by="user__name")
    task_name = tables.Column(verbose_name=_("Task Name"), empty_values=())
    date_assigned = DMYTColumn(verbose_name=_("Date Assigned"))
    due_date = DMYTColumn(verbose_name=_("Due Date"), accessor="task_due_date")
    task_status = TaskStatusColumn(verbose_name=_("Task Status"), empty_values=())

    group_item_order_by = ("user__name", "assignedtask__date_created")
    header_columns = ("index", "worker_status", "user")
    group_item_label = (gettext_lazy("task"), gettext_lazy("tasks"))

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
        empty_text = gettext_lazy("No tasks assigned for this opportunity.")
        template_name = "grouped_table.html"

    def __init__(self, data, *args, **kwargs):
        self.opp_id = kwargs.pop("opp_id")
        super().__init__(data, *args, **kwargs)


class WorkerLearnTable(OrgContextTable):
    index = IndexColumn()
    user = UserInfoColumn()
    status = StatusIndicatorColumn(orderable=False)
    last_active = DMYTColumn()
    started_learning = DMYTColumn(accessor="date_learn_started", verbose_name="Started Learning")
    modules_completed = tables.TemplateColumn(
        accessor="modules_completed_percentage",
        template_code="""
            {% include "components/progressbar/simple-progressbar.html" with text=flag percentage=value|default:0 %}
        """,
    )
    completed_learning = DMYTColumn(accessor="completed_learn_date", verbose_name="Completed Learning")
    assessment = tables.Column(accessor="assessment_status_rank")

    attempts = tables.Column(accessor="assesment_count")
    learning_hours = DurationColumn()
    action = tables.TemplateColumn(
        verbose_name="",
        orderable=False,
        attrs={"th": {"class": "col-action"}, "td": {"class": "col-action"}},
        template_code="""
        """,
    )

    def __init__(self, *args, **kwargs):
        self.use_view_url = False
        self.opp_id = kwargs.pop("opp_id")
        super().__init__(*args, **kwargs)

    class Meta:
        model = OpportunityAccess
        fields = ("user",)
        sequence = (
            "index",
            "status",
            "user",
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
        delivered = sum(row.delivered for row in table.data)
        incomplete = sum(row.incomplete for row in table.data)
        over_limit = sum(row.over_limit for row in table.data)
        return _render_delivered_breakdown(delivered, incomplete, over_limit)


def _render_delivered_breakdown(delivered, incomplete, over_limit, show_over_limit_warning=False):
    rows = [
        {"label": _("Completed"), "value": delivered - over_limit},
        {"label": _("Incomplete"), "value": incomplete},
        {"label": _("Over limit"), "value": over_limit},
    ]
    return render_to_string(
        "components/worker_page/deliver_column.html",
        {
            "value": delivered,
            "rows": rows,
            "over_limit": show_over_limit_warning and over_limit > 0,
        },
    )


class WorkerDeliveryTable(OrgContextTable):
    """Renders `WorkerDelivery` records: one row per worker, expanding to a row per payment unit."""

    index = IndexColumn()
    user = tables.Column(accessor="opportunity_access__user", orderable=False, verbose_name="Name", footer="Total")
    status = StatusIndicatorColumn(orderable=False)
    last_active = DMYTColumn(accessor="opportunity_access__last_active", empty_values=())
    payment_unit = tables.Column(accessor="payment_unit_label", orderable=False)
    delivery_progress = tables.Column(
        verbose_name=header_with_tooltip(
            "Delivery Progress", "Pending and approved payment units against the worker's allocation"
        ),
        accessor="allocation",
        empty_values=(),
        orderable=False,
    )
    delivered = TotalDeliveredColumn(
        verbose_name=header_with_tooltip("Delivered", "Delivered number of payment units"),
        accessor="delivered",
    )
    pending = TotalFlagCountsColumn(
        verbose_name=header_with_tooltip("Pending", "Payment units with pending approvals with NM or PM"),
        status=CompletedWorkStatus.pending,
    )
    approved = TotalFlagCountsColumn(
        verbose_name=header_with_tooltip(
            "Approved", "Payment units that are fully approved automatically or manually by NM and PM"
        ),
        status=CompletedWorkStatus.approved,
    )
    rejected = TotalFlagCountsColumn(
        verbose_name=header_with_tooltip("Rejected", "Payment units that are rejected"),
        status=CompletedWorkStatus.rejected,
    )

    action = tables.TemplateColumn(
        verbose_name="",
        orderable=False,
        attrs={"th": {"class": "col-action"}, "td": {"class": "col-action"}},
        template_code="""

        """,
    )

    class Meta:
        sequence = (
            "index",
            "status",
            "user",
            "last_active",
            "payment_unit",
            "delivery_progress",
            "delivered",
            "pending",
            "approved",
            "rejected",
            "action",
        )
        order_by = ("user__name", "-last_active")
        row_attrs = {"class": "group"}
        template_name = "tables/worker_delivery_table.html"

    # Worker-level columns, left blank on a worker's per-payment-unit rows. The action opens all of the
    # worker's visits, so repeating it per unit would suggest the visits are filtered to that unit.
    header_columns = ("index", "status", "user", "last_active", "action")

    def __init__(self, *args, **kwargs):
        self.opp_id = kwargs.pop("opp_id")
        self.use_view_url = False
        super().__init__(*args, **kwargs)
        for record in self.data:
            record.bound_sub_rows = [GroupedTable.SubBoundRow(record=row, table=self) for row in record.sub_rows]

    @property
    def has_over_limit(self):
        return any(record.over_limit for record in self.data)

    def render_payment_unit(self, value, record):
        if not record.sub_rows:
            return value
        return format_html(
            '<button type="button" @click="open = !open" class="flex items-center gap-2 cursor-pointer" '
            ':aria-expanded="open">'
            '{}<i class="fa-solid fa-chevron-down text-xs transition-transform duration-200" '
            ":class=\"{{'rotate-180': open}}\"></i></button>",
            value,
        )

    def render_user(self, value, record):
        return format_html(
            '<a href="{}" class="w-40">'
            '<p class="text-sm text-slate-900">{}</p>'
            '<p class="text-xs text-slate-400">{}</p>'
            "</a>",
            self._user_visits_url(record),
            value.name,
            value.username,
        )

    def render_delivery_progress(self, record):
        current = record.progress
        total = record.allocation

        if not total:
            return "-"

        percentage = min(round((current / total) * 100, 2), 100)

        context = {
            "current": current,
            "percentage": percentage,
            "total": total,
            "number_style": True,
        }

        return render_to_string("components/progressbar/simple-progressbar.html", context)

    def render_action(self, record):
        url = self._user_visits_url(record)
        template = """
            <div class="opacity-0 group-hover:opacity-100 transition-opacity duration-200 text-end">
                <a href="{}"><i class="fa-solid fa-chevron-right text-brand-deep-purple"></i></a>
            </div>
        """
        return format_html(template, url)

    def render_delivered(self, record):
        return _render_delivered_breakdown(
            record.delivered, record.incomplete, record.over_limit, show_over_limit_warning=True
        )

    def _render_flag_counts(self, record, value, status):
        url = reverse("opportunity:worker_flag_counts", args=[self.org_slug, self.opp_id])

        # Limit flag counts to the row's payment units, which the filters may have narrowed.
        params = {
            "status": status,
            "access_id": record.opportunity_access.opportunity_access_id,
            "payment_unit_id": record.payment_unit_ids,
        }
        full_url = f"{url}?{urlencode(params, doseq=True)}"

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

    def _user_visits_url(self, record):
        base_url = reverse("opportunity:user_visits_list", args=(self.org_slug, self.opp_id))
        return f"{base_url}?{urlencode({'user': record.opportunity_access.user.user_id})}"


class WorkerLearnStatusTable(tables.Table):
    index = IndexColumn()
    module_name = tables.Column(accessor="module__name", orderable=False)
    duration = DurationColumn(accessor="duration", orderable=False)
    date = DMYTColumn(verbose_name="Date Completed", accessor="date", orderable=False)

    class Meta:
        sequence = ("index", "module_name", "duration", "date")


class WorkerCompletedTaskTable(BaseAssignedTaskTable):
    use_view_url = True

    task_type = tables.Column(verbose_name=_("Task Type"), accessor="task_type", orderable=False)

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
