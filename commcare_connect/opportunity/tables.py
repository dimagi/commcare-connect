from crispy_forms.helper import FormHelper
from crispy_forms.layout import Column, Layout, Row
from django.urls import reverse
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django_filters import ChoiceFilter, DateRangeFilter, FilterSet, ModelChoiceFilter
from django_tables2 import columns, tables, utils

from commcare_connect.opportunity.models import (
    CatchmentArea,
    CompletedWork,
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


class OrgContextTable(tables.Table):
    def __init__(self, *args, **kwargs):
        self.org_slug = kwargs.pop("org_slug", None)
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
            "opportunity:user_payments_table",
            kwargs={"org_slug": self.org_slug, "opp_id": record.opportunity.id, "pk": record.pk},
        )
        return mark_safe(f'<a href="{url}">View Details</a>')

    class Meta:
        model = OpportunityAccess
        fields = ("display_name", "username", "payment_accrued", "total_paid", "total_confirmed_paid")
        orderable = False
        empty_text = "No user have payments accrued yet."


class UserPaymentsTable(tables.Table):
    class Meta:
        model = Payment
        fields = ("amount", "date_paid", "payment_method", "payment_operator")
        orderable = False
        empty_text = "No payments made for this user"
        template_name = "django_tables2/bootstrap5.html"


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


class PaymentUnitTable(OrgContextTable):
    deliver_units = columns.Column("Deliver Units")
    details = columns.Column(verbose_name="", empty_values=())

    class Meta:
        model = PaymentUnit
        fields = ("name", "amount")
        empty_text = "No payment units for this opportunity."
        orderable = False

    def render_deliver_units(self, record):
        deliver_units = "".join([f"<li>{d.name}</li>" for d in record.deliver_units.all()])
        return mark_safe(f"<ul>{deliver_units}</ul>")

    def render_details(self, record):
        url = reverse(
            "opportunity:edit_payment_unit",
            kwargs={"org_slug": self.org_slug, "opp_id": record.opportunity.id, "pk": record.pk},
        )
        return mark_safe(f'<a href="{url}">Edit</a>')


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


class PaymentInvoiceTable(tables.Table):
    pk = columns.CheckBoxColumn(
        accessor="pk",
        verbose_name="",
        attrs={
            "input": {"x-model": "selected"},
            "th__input": {"@click": "toggleSelectAll()", "x-bind:checked": "selectAll"},
        },
    )
    payment_status = columns.Column(verbose_name="Payment Status", accessor="payment", empty_values=())
    payment_date = columns.Column(verbose_name="Payment Date", accessor="payment", empty_values=(None))

    class Meta:
        model = PaymentInvoice
        orderable = False
        fields = ("pk", "amount", "date", "invoice_number", "service_delivery")
        sequence = ("pk", "amount", "date", "invoice_number", "payment_status", "payment_date", "service_delivery")
        empty_text = "No Payment Invoices"

    def render_payment_status(self, value):
        if value is not None:
            return "Paid"
        return "Pending"

    def render_payment_date(self, value):
        if value is not None:
            return value.date_paid
        return


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
