from django.contrib import admin

from commcare_connect.program.models import Program, ProgramApplication, ProgramFunderEvent, ProgramWatcherEvent
from commcare_connect.utils.admin import AuditEventAdmin, audited_object


class ProgramApplicationInline(admin.TabularInline):
    list_display = ("organization", "status", "date_created")
    model = ProgramApplication


@admin.register(Program)
class ProgramAdmin(admin.ModelAdmin):
    list_display = ("name", "organization")
    inlines = [ProgramApplicationInline]
    search_fields = ["name"]


@admin.register(ProgramFunderEvent)
class ProgramFunderEventAdmin(AuditEventAdmin):
    list_display = ("pgh_created_at", "program", "funder", "pgh_label", "changed_by")
    list_filter = (
        "pgh_label",
        ("funder", admin.RelatedOnlyFieldListFilter),
        ("pgh_obj", admin.RelatedOnlyFieldListFilter),
    )
    search_fields = ("pgh_obj__name", "funder__name")
    fields = ("pgh_created_at", "program", "funder", "pgh_label", "changed_by", "changed_by_email")
    readonly_fields = fields

    program = audited_object("pgh_obj", "Program")

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("pgh_obj", "funder")


@admin.register(ProgramWatcherEvent)
class ProgramWatcherEventAdmin(AuditEventAdmin):
    # `program` and `organization` are snapshotted on the event itself, so unlike pgh_obj
    # they still resolve for a delete, when the through row they described is gone.
    list_display = ("pgh_created_at", "program", "organization", "pgh_label", "changed_by")
    list_filter = (
        "pgh_label",
        ("organization", admin.RelatedOnlyFieldListFilter),
        ("program", admin.RelatedOnlyFieldListFilter),
    )
    search_fields = ("program__name", "organization__name")
    fields = ("pgh_created_at", "program", "organization", "pgh_label", "changed_by", "changed_by_email")
    readonly_fields = fields

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("program", "organization")
