from django.contrib import admin

from commcare_connect.microplanning.models import WorkArea


@admin.register(WorkArea)
class WorkAreaAdmin(admin.ModelAdmin):
    list_display = ["opportunity__name", "slug", "opportunity_access__user__username"]
    search_fields = ["opportunity__name", "opportunity_access__user__username"]
