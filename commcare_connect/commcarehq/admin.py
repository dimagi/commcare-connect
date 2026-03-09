from django.contrib import admin

from .models import HQServer


@admin.register(HQServer)
class HQServerAdmin(admin.ModelAdmin):
    list_display = ["name", "url"]
    search_fields = ["name", "url"]
