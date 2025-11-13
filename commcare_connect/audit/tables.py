import django_tables2 as tables
from django.urls import reverse
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.utils.translation import gettext as _

from commcare_connect.audit.models import Audit
from commcare_connect.utils.tables import DMYTColumn, IndexColumn


class AuditTable(tables.Table):
    """Table for displaying audits"""

    index = IndexColumn()

    opportunity_name = tables.Column(
        verbose_name=_("Opportunity"),
        orderable=True,
        attrs={"td": {"class": "text-sm text-brand-deep-purple font-medium whitespace-normal break-words"}},
    )

    opportunity_ids = tables.Column(
        verbose_name=_("Opp IDs"), orderable=False, attrs={"td": {"class": "text-sm text-gray-600"}}
    )

    flw_username = tables.Column(
        verbose_name=_("FLW"), orderable=True, attrs={"td": {"class": "text-sm text-brand-deep-purple"}}
    )

    start_date = DMYTColumn(verbose_name=_("Start Date"))
    end_date = DMYTColumn(verbose_name=_("End Date"))

    status = tables.Column(
        verbose_name=_("Status"),
        orderable=True,
    )

    progress = tables.Column(
        verbose_name=_("Progress"),
        accessor="progress_percentage",
        orderable=False,
        attrs={"td": {"class": "text-center"}},
    )

    overall_result = tables.Column(
        verbose_name=_("Result"),
        orderable=True,
    )

    actions = tables.Column(verbose_name="", orderable=False, empty_values=(), attrs={"td": {"class": "text-right"}})

    class Meta:
        model = Audit
        fields = (
            "index",
            "opportunity_name",
            "opportunity_ids",
            "flw_username",
            "start_date",
            "end_date",
            "status",
            "progress",
            "overall_result",
            "actions",
        )
        sequence = (
            "index",
            "opportunity_name",
            "opportunity_ids",
            "flw_username",
            "start_date",
            "end_date",
            "status",
            "progress",
            "overall_result",
            "actions",
        )
        order_by = ("-created_at",)
        attrs = {
            "class": "base-table",
        }
        empty_text = _("No audits yet. Create your first audit to get started.")

    def render_opportunity_ids(self, value):
        """Render opportunity IDs as comma-separated list"""
        if not value:
            return "-"
        if isinstance(value, list) and len(value) > 0:
            return ", ".join(str(id) for id in value)
        return "-"

    def render_status(self, value, record):
        """Render status as badge"""
        if value == Audit.Status.COMPLETED:
            badge_class = "badge badge-sm bg-green-50 text-green-700"
            text = _("Completed")
        elif value == Audit.Status.IN_PROGRESS:
            badge_class = "badge badge-sm bg-orange-50 text-orange-700"
            text = _("In Progress")
        else:
            badge_class = "badge badge-sm bg-slate-100 text-slate-700"
            text = value

        return format_html('<span class="{}">{}</span>', badge_class, text)

    def render_progress(self, value, record):
        """Render progress percentage with counts"""
        formatted_value = f"{value:.1f}"
        total_visits = record.visits.count()

        # Count audited visits (where all assessments have been reviewed)
        audited_visits = 0
        for result in record.results.prefetch_related("assessments"):
            assessments = result.assessments.all()
            if assessments:
                # Has assessments - check if all are reviewed
                if all(assessment.result is not None for assessment in assessments):
                    audited_visits += 1
            else:
                # No assessments (e.g., no images) - consider it audited
                audited_visits += 1

        return format_html(
            '<div class="text-center">'
            '<div class="text-sm font-medium text-brand-deep-purple">{}%</div>'
            '<div class="text-xs text-gray-500">({} of {})</div>'
            "</div>",
            formatted_value,
            audited_visits,
            total_visits,
        )

    def render_overall_result(self, value, record):
        """Render overall result as badge with icon"""
        if not value:
            return "-"

        # Value could be "pass"/"fail" (db value) or "Pass"/"Fail" (display value)
        # Check both cases to be safe
        value_lower = value.lower() if isinstance(value, str) else ""

        if value_lower == "pass":
            badge_class = "badge badge-sm bg-green-50 text-green-700"
            icon = '<i class="fa-solid fa-thumbs-up mr-1"></i>'
            text = _("Pass")
        elif value_lower == "fail":
            badge_class = "badge badge-sm bg-red-50 text-red-700"
            icon = '<i class="fa-solid fa-thumbs-down mr-1"></i>'
            text = _("Fail")
        else:
            # Shouldn't happen, but show value for debugging
            return format_html('<span class="badge badge-sm bg-yellow-50 text-yellow-700">Unknown: {}</span>', value)

        return format_html('<span class="{}">{}{}</span>', badge_class, mark_safe(icon), text)

    def render_actions(self, record):
        """Render action buttons"""
        audit_url = reverse("audit:session_detail", kwargs={"pk": record.pk})
        bulk_url = reverse("audit:bulk_assessment", kwargs={"pk": record.pk})

        buttons = [
            format_html('<a href="{}" class="button button-sm primary-light">{}</a>', audit_url, _("Single")),
            format_html('<a href="{}" class="button button-sm outline-style">{}</a>', bulk_url, _("Bulk")),
        ]

        if record.status == Audit.Status.COMPLETED:
            export_url = reverse("audit:session_export", kwargs={"pk": record.pk})
            buttons.append(
                format_html('<a href="{}" class="button button-sm outline-style">{}</a>', export_url, _("Export"))
            )

        return format_html(
            '<div class="flex gap-2 justify-end">{}</div>', mark_safe(" ".join(str(btn) for btn in buttons))
        )


# Backward compatibility alias
AuditSessionTable = AuditTable
