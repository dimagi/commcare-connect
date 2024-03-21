from django.urls import reverse
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django_tables2 import columns, tables, utils

from commcare_connect.opportunity.models import (
    CompletedWork,
    OpportunityAccess,
    Payment,
    PaymentUnit,
    UserVisit,
    VisitValidationStatus,
)


class LearnStatusTable(tables.Table):
    display_name = columns.Column(verbose_name="Name")
    learn_progress = columns.Column(verbose_name="Modules Completed")
    details = columns.LinkColumn(
        "opportunity:user_learn_progress",
        verbose_name="",
        text="View Details",
        args=[utils.A("opportunity__organization__slug"), utils.A("opportunity__id"), utils.A("pk")],
    )

    class Meta:
        model = OpportunityAccess
        fields = ("display_name", "learn_progress")
        sequence = ("display_name", "learn_progress")
        orderable = False
        empty_text = "No learn progress for users."


def show_warning(record):
    if record.status not in (VisitValidationStatus.approved, VisitValidationStatus.rejected):
        if record.flagged:
            return "table-warning"
    return ""


class UserVisitTable(tables.Table):
    # export only columns
    visit_id = columns.Column("Visit ID", accessor="xform_id", visible=False)
    username = columns.Column("Username", accessor="user__username", visible=False)
    form_json = columns.Column("Form JSON", accessor="form_json", visible=False)
    visit_date_export = columns.DateTimeColumn(
        verbose_name="Visit date", accessor="visit_date", format="c", visible=False
    )
    reason = columns.Column("Rejected Reason", accessor="reason", visible=False)

    deliver_unit = columns.Column("Unit Name", accessor="deliver_unit__name")
    entity_id = columns.Column("Entity ID", accessor="entity_id", visible=False)
    entity_name = columns.Column("Entity Name", accessor="entity_name")
    flag_reason = columns.Column("Flags", accessor="flag_reason", empty_values=({}, None))
    details = columns.LinkColumn(
        "opportunity:visit_verification",
        verbose_name="",
        text="Review",
        attrs={"a": {"class": "btn btn-sm btn-primary"}},
        args=[utils.A("opportunity__organization__slug"), utils.A("pk")],
    )

    def render_flag_reason(self, value):
        short = [flag[0] for flag in value.get("flags")]
        return ", ".join(short)

    class Meta:
        model = UserVisit
        fields = ("user__name", "username", "visit_date", "status")
        sequence = (
            "visit_id",
            "visit_date",
            "visit_date_export",
            "status",
            "username",
            "user__name",
            "deliver_unit",
        )
        empty_text = "No forms."
        orderable = False
        row_attrs = {"class": show_warning}


class OpportunityPaymentTable(tables.Table):
    display_name = columns.Column(verbose_name="Name")
    username = columns.Column(accessor="user__username", visible=False)
    view_payments = columns.LinkColumn(
        "opportunity:user_payments_table",
        verbose_name="",
        text="View Details",
        args=[utils.A("opportunity__organization__slug"), utils.A("opportunity__id"), utils.A("pk")],
    )

    class Meta:
        model = OpportunityAccess
        fields = ("display_name", "username", "payment_accrued", "total_paid")
        orderable = False
        empty_text = "No user have payments accrued yet."


class UserPaymentsTable(tables.Table):
    class Meta:
        model = Payment
        fields = ("amount", "date_paid")
        orderable = False
        empty_text = "No payments made for this user"
        template_name = "django_tables2/bootstrap5.html"


class AggregateColumn(columns.Column):
    def render_footer(self, bound_column, table):
        return sum(1 if bound_column.accessor.resolve(row) else 0 for row in table.data)


class BooleanAggregateColumn(columns.BooleanColumn, AggregateColumn):
    pass


class UserStatusTable(tables.Table):
    display_name = columns.Column(verbose_name="Name", footer="Total")
    username = columns.Column(accessor="user__username", visible=False)
    accepted = BooleanAggregateColumn(verbose_name="Accepted")
    claimed = AggregateColumn(verbose_name="Job Claimed", accessor="job_claimed")
    started_learning = AggregateColumn(verbose_name="Started Learning", accessor="date_learn_started")
    completed_learning = AggregateColumn(verbose_name="Completed Learning", accessor="date_learn_completed")
    passed_assessment = BooleanAggregateColumn(verbose_name="Passed Assessment")
    started_delivery = AggregateColumn(verbose_name="Started Delivery", accessor="date_deliver_started")
    last_visit_date = columns.Column(accessor="last_visit_date_d")
    view_profile = AggregateColumn("View Profile", empty_values=(), footer=lambda table: len(table.rows))

    class Meta:
        model = OpportunityAccess
        fields = ("display_name", "accepted", "last_visit_date")
        sequence = (
            "display_name",
            "username",
            "accepted",
            "started_learning",
            "completed_learning",
            "passed_assessment",
            "claimed",
            "started_delivery",
            "last_visit_date",
        )
        empty_text = "No users invited for this opportunity."
        orderable = False

    def render_view_profile(self, record):
        if not record.accepted:
            return "---"
        url = reverse(
            "opportunity:user_profile",
            kwargs={
                "org_slug": record.opportunity.organization.slug,
                "opp_id": record.opportunity_id,
                "pk": record.id,
            },
        )
        return format_html('<a href="{}">View Profile</a>', url)

    def render_started_learning(self, record, value):
        return date_with_time_popup(self, value)

    def render_completed_learning(self, record, value):
        return date_with_time_popup(self, value)

    def render_started_delivery(self, record, value):
        return date_with_time_popup(self, value)

    def render_last_visit_date(self, record, value):
        return date_with_time_popup(self, value)


class PaymentUnitTable(tables.Table):
    deliver_units = columns.Column("Deliver Units")
    details = columns.LinkColumn(
        "opportunity:edit_payment_unit",
        verbose_name="",
        text="Edit",
        args=[utils.A("opportunity__organization__slug"), utils.A("opportunity__id"), utils.A("pk")],
    )

    class Meta:
        model = PaymentUnit
        fields = ("name", "amount")
        empty_text = "No payment units for this opportunity."
        orderable = False

    def render_deliver_units(self, record):
        deliver_units = "".join([f"<li>{d.name}</li>" for d in record.deliver_units.all()])
        return mark_safe(f"<ul>{deliver_units}</ul>")


class DeliverStatusTable(tables.Table):
    display_name = columns.Column("Name of the User")
    username = columns.Column(accessor="user__username", visible=False)
    visits_completed = columns.Column("Completed Visits")
    visits_approved = columns.Column("Approved Visits")
    visits_pending = columns.Column("Pending Visits")
    visits_rejected = columns.Column("Rejected Visits")
    visits_over_limit = columns.Column("Over Limit Visits")
    visits_duplicate = columns.Column("Duplicate Visits")
    details = columns.LinkColumn(
        "opportunity:user_visits_list",
        verbose_name="",
        text="View Details",
        args=[utils.A("opportunity__organization__slug"), utils.A("opportunity__id"), utils.A("pk")],
    )

    class Meta:
        model = OpportunityAccess
        fields = ("last_visit_date",)
        orderable = False
        sequence = (
            "display_name",
            "username",
            "visits_completed",
            "visits_approved",
            "visits_pending",
            "visits_rejected",
            "visits_over_limit",
            "visits_duplicate",
            "last_visit_date",
        )

    def render_last_visit_date(self, record, value):
        return date_with_time_popup(self, value)


class CompletedWorkTable(tables.Table):
    id = columns.Column("Instance Id", visible=False)
    username = columns.Column(accessor="opportunity_access__user__username", visible=False)
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
