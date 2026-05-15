from django.contrib import admin

from commcare_connect.microplanning.models import WorkArea


@admin.register(WorkArea)
class WorkAreaAdmin(admin.ModelAdmin):
    list_display = ["get_opp_name", "slug", "get_username"]
    search_fields = ["opportunity__name", "opportunity_access__user__username"]

    @admin.display(description="Opportunity Name")
    def get_opp_name(self, obj):
        return obj.opportunity.name

    @admin.display(description="Username")
    def get_username(self, obj):
        if obj.opportunity_access:
            return obj.opportunity_access.user.username
        return "-"
