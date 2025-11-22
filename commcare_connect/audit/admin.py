from django.contrib import admin
from django.utils.html import format_html

from commcare_connect.audit.models import Assessment, Audit, AuditResult, AuditTemplate


@admin.register(AuditTemplate)
class AuditTemplateAdmin(admin.ModelAdmin):
    list_display = ["id", "name", "audit_type", "granularity", "created_at"]
    list_filter = ["audit_type", "granularity", "created_at"]
    search_fields = ["name", "description"]
    readonly_fields = ["created_at", "updated_at", "expires_at"]

    fieldsets = (
        (None, {"fields": ("name", "description", "created_by")}),
        ("Configuration", {"fields": ("opportunity_ids", "audit_type", "granularity")}),
        ("Criteria", {"fields": ("start_date", "end_date", "count_per_flw", "count_per_opp", "count_across_all")}),
        ("Sampling", {"fields": ("sample_percentage", "sampled_visit_ids")}),
        ("Timestamps", {"fields": ("created_at", "updated_at", "expires_at"), "classes": ("collapse",)}),
    )


@admin.register(Audit)
class AuditAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "flw_username_display",
        "opportunity_name",
        "domain_display",
        "date_range",
        "status",
        "overall_result",
        "progress",
        "created_at",
    ]
    list_filter = ["status", "overall_result", "created_at"]
    search_fields = ["auditor__username", "opportunity_name", "title", "tag"]
    readonly_fields = ["created_at", "progress", "computed_fields"]

    fieldsets = (
        (None, {"fields": ("auditor", "primary_opportunity", "opportunity_name")}),
        ("Categorization", {"fields": ("title", "tag")}),
        ("Audit Period", {"fields": ("start_date", "end_date")}),
        ("Results", {"fields": ("status", "overall_result", "notes", "kpi_notes")}),
        ("Computed Fields (Read-only)", {"fields": ("computed_fields",), "classes": ("collapse",)}),
        ("Timestamps", {"fields": ("created_at", "completed_at"), "classes": ("collapse",)}),
    )

    @admin.display(description="FLW Username")
    def flw_username_display(self, obj):
        return obj.flw_username or "Multiple FLWs"

    @admin.display(description="Domain")
    def domain_display(self, obj):
        return obj.domain or "N/A"

    @admin.display(description="Computed Fields")
    def computed_fields(self, obj):
        return format_html(
            "<strong>FLW:</strong> {}<br>"
            "<strong>Domain:</strong> {}<br>"
            "<strong>App ID:</strong> {}<br>"
            "<strong>Opportunity IDs:</strong> {}<br>"
            "<strong>User IDs:</strong> {}",
            obj.flw_username or "N/A",
            obj.domain or "N/A",
            obj.app_id or "N/A",
            ", ".join(map(str, obj.opportunity_ids)) if obj.opportunity_ids else "N/A",
            ", ".join(map(str, obj.user_ids[:5])) + ("..." if len(obj.user_ids) > 5 else "")
            if obj.user_ids
            else "N/A",
        )

    @admin.display(description="Date Range")
    def date_range(self, obj):
        return f"{obj.start_date} to {obj.end_date}"

    @admin.display(description="Progress")
    def progress(self, obj):
        percentage = obj.progress_percentage
        total_visits = obj.visits.count()
        audited_visits = obj.results.count()

        if percentage == 100:
            color = "green"
        elif percentage > 50:
            color = "orange"
        else:
            color = "red"

        return format_html(
            '<span style="color: {};">{:.1f}% ({}/{})</span>', color, percentage, audited_visits, total_visits
        )


# AuditVisit removed - using UserVisit directly for full production compatibility


# AuditVisitImage removed - using BlobMeta instead for consistency with production


@admin.register(AuditResult)
class AuditResultAdmin(admin.ModelAdmin):
    list_display = ["id", "audit", "user_visit", "visit_date", "result", "has_notes", "audited_at"]
    list_filter = ["result", "audit__status", "audited_at"]
    search_fields = ["user_visit__xform_id", "notes"]
    readonly_fields = ["audited_at"]

    @admin.display(description="Visit Date")
    def visit_date(self, obj):
        return obj.user_visit.visit_date.strftime("%Y-%m-%d %H:%M")

    @admin.display(
        description="Has Notes",
        boolean=True,
    )
    def has_notes(self, obj):
        return bool(obj.notes)


@admin.register(Assessment)
class AssessmentAdmin(admin.ModelAdmin):
    list_display = ["id", "audit_result", "assessment_type", "question_id", "result", "has_notes", "created_at"]
    list_filter = ["assessment_type", "result", "created_at"]
    search_fields = ["blob_id", "question_id", "notes"]
    readonly_fields = ["created_at", "updated_at"]

    fieldsets = (
        (None, {"fields": ("audit_result", "assessment_type")}),
        ("Image Assessment", {"fields": ("blob_id", "question_id")}),
        ("Result", {"fields": ("result", "notes")}),
        ("Config", {"fields": ("config_data",), "classes": ("collapse",)}),
        ("Timestamps", {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )

    @admin.display(
        description="Has Notes",
        boolean=True,
    )
    def has_notes(self, obj):
        return bool(obj.notes)
