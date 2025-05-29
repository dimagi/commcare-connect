from django.contrib import admin

from commcare_connect.program.models import Program, ProgramApplication


class ProgramApplicationInline(admin.TabularInline):
    list_display = ("organization", "status", "date_created")
    model = ProgramApplication


@admin.register(Program)
class ProgramAdmin(admin.ModelAdmin):
    list_display = ("name", "organization")
    inlines = [ProgramApplicationInline]
    search_fields = ["name"]
