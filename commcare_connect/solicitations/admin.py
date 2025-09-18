from django.contrib import admin

from .models import Solicitation, SolicitationQuestion, SolicitationResponse, SolicitationReview


@admin.register(Solicitation)
class SolicitationAdmin(admin.ModelAdmin):
    list_display = ["title", "solicitation_type", "status", "is_publicly_listed", "program", "application_deadline"]
    list_filter = ["solicitation_type", "status", "is_publicly_listed", "program__organization"]
    search_fields = ["title", "description", "target_population"]
    readonly_fields = ["created_by", "date_created", "date_modified"]

    fieldsets = (
        ("Basic Information", {"fields": ("title", "solicitation_type", "program", "created_by")}),
        ("Content", {"fields": ("description", "target_population", "scope_of_work", "estimated_scale")}),
        ("Timeline", {"fields": ("expected_start_date", "expected_end_date", "application_deadline")}),
        ("Status", {"fields": ("status", "is_publicly_listed")}),
        ("Files", {"fields": ("attachments",)}),
        ("Metadata", {"fields": ("date_created", "date_modified"), "classes": ("collapse",)}),
    )


@admin.register(SolicitationQuestion)
class SolicitationQuestionAdmin(admin.ModelAdmin):
    list_display = ["solicitation", "question_text_short", "question_type", "is_required", "order"]
    list_filter = ["question_type", "is_required", "solicitation__solicitation_type"]
    search_fields = ["question_text", "solicitation__title"]
    list_editable = ["order", "is_required"]

    @admin.display(description="Question")
    def question_text_short(self, obj):
        return obj.question_text[:50] + "..." if len(obj.question_text) > 50 else obj.question_text


@admin.register(SolicitationResponse)
class SolicitationResponseAdmin(admin.ModelAdmin):
    list_display = ["solicitation", "organization", "submitted_by", "status", "submission_date"]
    list_filter = ["status", "submission_date", "solicitation__solicitation_type"]
    search_fields = ["solicitation__title", "organization__name", "submitted_by__email"]
    readonly_fields = ["submission_date", "responses"]

    fieldsets = (
        ("Basic Information", {"fields": ("solicitation", "organization", "submitted_by", "submission_date")}),
        ("Status", {"fields": ("status",)}),
        ("Responses", {"fields": ("responses",), "classes": ("collapse",)}),
        ("Files", {"fields": ("attachments",)}),
    )


@admin.register(SolicitationReview)
class SolicitationReviewAdmin(admin.ModelAdmin):
    list_display = ["response", "reviewer", "score", "recommendation", "review_date"]
    list_filter = ["recommendation", "review_date", "response__solicitation__solicitation_type"]
    search_fields = ["response__solicitation__title", "response__organization__name", "reviewer__email"]
    readonly_fields = ["review_date"]
