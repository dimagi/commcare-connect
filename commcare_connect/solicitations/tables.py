import django_tables2 as tables
from django.urls import reverse
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from commcare_connect.solicitations.models import Solicitation, SolicitationResponse
from commcare_connect.utils.tables import OrgContextTable

# =============================================================================
# Utility Functions
# =============================================================================


def get_status_badge_class(status):
    """Get CSS class for all badge types (statuses, recommendations, solicitation types)"""
    status_classes = {
        # Response statuses
        "draft": "badge badge-sm bg-slate-100 text-slate-400",
        "submitted": "badge badge-sm bg-blue-600/20 text-blue-600",
        # Solicitation statuses
        "active": "badge badge-sm bg-green-600/20 text-green-600",
        "closed": "badge badge-sm bg-red-600/20 text-red-600",
        # Review recommendations
        "recommended": "badge badge-sm bg-green-600/20 text-green-600",
        "not_recommended": "badge badge-sm bg-orange-600/20 text-orange-600",
        "neutral": "badge badge-sm bg-violet-500/20 text-violet-500",
        # Solicitation types
        "eoi": "badge badge-sm bg-green-600/20 text-green-600",
        "rfp": "badge badge-sm bg-orange-600/20 text-orange-600",
    }
    return status_classes.get(status, "badge badge-sm bg-slate-100 text-slate-400")


def render_text_with_badge(title, solicitation_type):
    """Render solicitation title with type badge at the end"""
    badge_class = get_status_badge_class(solicitation_type)
    badge_text = solicitation_type.upper()
    return format_html('<div class="text-wrap">{} <span class="{}">{}</span></div>', title, badge_class, badge_text)


def render_two_line_text(title, subtitle=None):
    """Render two-line text content"""
    if subtitle:
        return format_html(
            '<div><div class="font-medium">{}</div><div class="text-xs text-gray-500">{}</div></div>',
            title,
            mark_safe(subtitle) if isinstance(subtitle, str) and ("<" in subtitle and ">" in subtitle) else subtitle,
        )
    return format_html('<div class="font-medium">{}</div>', title)


def create_action_link(url, icon, title, target="_self"):
    """Create a standardized action link"""
    target_attr = f' target="{target}"' if target != "_self" else ""
    return (
        f'<a href="{url}" class="text-brand-indigo hover:text-brand-deep-purple" title="{title}"{target_attr}>'
        f'<i class="fa-solid {icon}"></i></a>'
    )


# =============================================================================
# Program  Table - shown when user is in admin or user mode for dashboard
# =============================================================================
class ProgramTable(tables.Table):
    """Table for displaying programs where user can create solicitations"""

    name = tables.Column(verbose_name="Program Name")
    organization = tables.Column(accessor="organization__name")
    active_solicitations = tables.Column(empty_values=(), verbose_name="Active Solicitations", orderable=False)
    total_responses = tables.Column(empty_values=(), verbose_name="Total Responses", orderable=False)
    actions = tables.Column(empty_values=(), verbose_name="Actions", orderable=False)

    class Meta:
        model = None  # Will be set dynamically to Program model
        fields = ("name", "organization", "active_solicitations", "total_responses", "actions")
        order_by = ("name",)
        template_name = "base_table.html"

    def __init__(self, *args, **kwargs):
        self.org_slug = kwargs.pop("org_slug", "")
        super().__init__(*args, **kwargs)

    def render_name(self, value, record):
        """Render program name with description"""
        description = getattr(record, "description", "") or "No description available"
        return render_two_line_text(value, description[:50] + "..." if len(description) > 50 else description)

    def render_organization(self, value, record):
        """Render organization name"""
        return render_two_line_text(value, "Program Manager Organization")

    def render_active_solicitations(self, record):
        """Render count of active solicitations for this program"""
        return getattr(record, "active_solicitations_count", 0)

    def render_total_responses(self, record):
        """Render total responses across all solicitations for this program"""
        return getattr(record, "total_responses_count", 0)

    def render_actions(self, record):
        """Render action links for program management"""
        # Create new solicitation
        create_url = reverse(
            "org_solicitations:program_solicitation_create",
            kwargs={
                "org_slug": record.organization.slug,
                "program_pk": record.pk,
            },
        )

        # View program dashboard
        dashboard_url = reverse(
            "org_solicitations:program_dashboard",
            kwargs={
                "org_slug": record.organization.slug,
                "pk": record.pk,
            },
        )

        actions = [
            create_action_link(create_url, "fa-plus-circle", "Create Solicitation"),
            create_action_link(dashboard_url, "fa-eye", "View Dashboard"),
        ]

        return format_html('<div class="flex items-center space-x-2">{}</div>', mark_safe("".join(actions)))


# =============================================================================
# Table Classes
# =============================================================================
class SolicitationTable(OrgContextTable):
    """Table for displaying solicitations in admin overview and public views"""

    def __init__(self, *args, **kwargs):
        self.show_program_org = kwargs.pop("show_program_org", True)
        super().__init__(*args, **kwargs)

        # Hide program_org column for program dashboard
        if not self.show_program_org:
            self.columns.hide("program_org")

    program_org = tables.Column(empty_values=(), verbose_name="Program & Org", orderable=False)
    total = tables.Column(empty_values=(), verbose_name="Total", orderable=False)
    reviewed = tables.Column(empty_values=(), verbose_name="Reviewed", orderable=False)
    actions = tables.Column(empty_values=(), verbose_name="Actions", orderable=False)

    class Meta:
        model = Solicitation
        fields = ("title", "program_org", "status", "application_deadline", "total", "reviewed", "actions")
        order_by = ("-application_deadline",)
        template_name = "base_table.html"

    def render_title(self, value, record):
        """Render solicitation title with type badge"""
        return render_text_with_badge(value, record.solicitation_type)

    def render_program_org(self, record):
        """Render program name and organization"""
        return render_two_line_text(record.program.name, record.program.organization.name)

    def render_application_deadline(self, value):
        """Render deadline"""
        return value.strftime("%d-%b-%Y") if value else "—"

    def render_total(self, record):
        """Render total responses count"""
        total = getattr(record, "total_responses", 0)
        return format_html('<span class="font-medium">{}</span>', total)

    def render_reviewed(self, record):
        """Render reviewed responses count"""
        # With simplified status, this shows total submitted responses
        submitted = getattr(record, "submitted_count", 0)
        return format_html('<span class="font-medium">{}</span>', submitted)

    def render_actions(self, record):
        """Render action links"""
        actions = []

        # Public view link
        public_url = reverse("solicitations:detail", kwargs={"pk": record.pk})
        actions.append(create_action_link(public_url, "fa-external-link-alt", "View Public Page", target="_blank"))

        # Management actions (if user has permissions)
        if hasattr(record, "program") and hasattr(record.program, "organization"):
            org_slug = record.program.organization.slug

            edit_url = reverse(
                "org_solicitations:program_solicitation_edit",
                kwargs={"org_slug": org_slug, "program_pk": record.program.pk, "pk": record.pk},
            )
            responses_url = reverse(
                "org_solicitations:program_response_list",
                kwargs={"org_slug": org_slug, "pk": record.program.pk, "solicitation_pk": record.pk},
            )

            actions.extend(
                [
                    create_action_link(edit_url, "fa-edit", "Edit Solicitation"),
                    create_action_link(responses_url, "fa-inbox", "View Responses"),
                ]
            )

        return format_html('<div class="flex items-center space-x-2">{}</div>', mark_safe("".join(actions)))


class SolicitationResponseAndReviewTable(OrgContextTable):
    """Combined table showing solicitation responses with their review data"""

    def __init__(self, *args, **kwargs):
        self.mode = kwargs.pop("mode", "user")  # 'admin', 'program', 'user'
        self.program_pk = kwargs.pop("program_pk", None)
        self.user = kwargs.pop("user", None)  # For permission checking
        super().__init__(*args, **kwargs)

    solicitation = tables.Column(accessor="solicitation__title", verbose_name="Solicitation")
    submitting_org = tables.Column(empty_values=(), verbose_name="Submitting Org", orderable=False)
    submitted_by = tables.Column(verbose_name="Submitted By")
    last_edited = tables.Column(accessor="submission_date", verbose_name="Last Edited")
    recommendation = tables.Column(empty_values=(), verbose_name="Recommendation", orderable=False)
    score = tables.Column(empty_values=(), verbose_name="Score", orderable=False)
    actions = tables.Column(empty_values=(), verbose_name="Actions", orderable=False)

    class Meta:
        model = SolicitationResponse
        fields = (
            "solicitation",
            "submitting_org",
            "submitted_by",
            "last_edited",
            "recommendation",
            "score",
            "actions",
        )
        order_by = ("-last_edited",)
        template_name = "base_table.html"

    def render_solicitation(self, value, record):
        """Render solicitation title with type badge"""
        return render_text_with_badge(value, record.solicitation.solicitation_type)

    def render_submitting_org(self, record):
        """Render submitting organization name"""
        return record.organization.name

    def render_submitted_by(self, value, record):
        """Render submitted by user info"""
        if not value:
            return "—"
        name = value.get_full_name() or value.email
        return render_two_line_text(name, value.email)

    def render_last_edited(self, value):
        """Render last edit date and time on separate lines"""
        if not value:
            return "—"
        date_str = value.strftime("%d-%b-%Y")
        time_str = value.strftime("%I:%M %p").lower()
        return render_two_line_text(date_str, time_str)

    def render_recommendation(self, record):
        """Render recommendation from review if it exists"""
        try:
            review = record.reviews.first()
            if review and review.recommendation:
                badge_class = get_status_badge_class(review.recommendation)
                return format_html('<span class="{}">{}</span>', badge_class, review.get_recommendation_display())
        except Exception:
            pass
        return "—"

    def render_score(self, record):
        """Render score from review if it exists"""
        try:
            review = record.reviews.first()
            if review and review.score is not None:
                return review.score
        except Exception:
            pass
        return "—"

    def render_actions(self, record):
        """Render action links based on mode and permissions"""
        actions = []

        if self.mode == "admin":
            # Admin can always view in admin
            url = f"/admin/solicitations/solicitationresponse/{record.pk}/change/"
            actions.append(create_action_link(url, "fa-eye", "View in Admin"))

        elif self.mode == "program":
            # Program managers can review responses
            url = reverse(
                "org_solicitations:program_response_review",
                kwargs={"org_slug": self.org_slug, "pk": self.program_pk, "response_pk": record.pk},
            )
            actions.append(create_action_link(url, "fa-pen-to-square", "Review Response"))

        else:  # user mode
            # Users can edit/view their own responses
            response_org_slug = record.organization.slug
            if record.status == SolicitationResponse.Status.DRAFT:
                url = reverse(
                    "org_solicitations:user_response_edit",
                    kwargs={"org_slug": response_org_slug, "pk": record.pk},
                )
                actions.append(create_action_link(url, "fa-pen-to-square", "Edit Response"))
            else:
                url = reverse(
                    "org_solicitations:user_response_detail",
                    kwargs={"org_slug": response_org_slug, "pk": record.pk},
                )
                actions.append(create_action_link(url, "fa-eye", "View Response"))

            # Check if user can review this response
            if self._user_can_review_response(record):
                review_url = reverse(
                    "org_solicitations:program_response_review",
                    kwargs={
                        "org_slug": record.solicitation.program.organization.slug,
                        "pk": record.solicitation.program.pk,
                        "response_pk": record.pk,
                    },
                )
                actions.append(create_action_link(review_url, "fa-clipboard-check", "Review"))

        return format_html('<div class="flex items-center space-x-2">{}</div>', mark_safe("".join(actions)))

    def _user_can_review_response(self, response):
        """Check if the current user can review this response"""
        if not self.user:
            return False

        # User can review if they are a program manager for the solicitation's program
        program = response.solicitation.program
        program_org = program.organization

        # Check if user is admin of the program organization and org is program manager
        for membership in self.user.memberships.all():
            if membership.organization == program_org and membership.is_admin and program_org.program_manager:
                return True
        return False
