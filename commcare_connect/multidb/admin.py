from django import forms
from django.contrib import admin, messages

from commcare_connect.multidb.models import SupersetReplicatedTable
from commcare_connect.multidb.services import get_model_label_choices, sync_replicated_tables


class SupersetReplicatedTableForm(forms.ModelForm):
    class Meta:
        model = SupersetReplicatedTable
        fields = ["model_label"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["model_label"] = forms.ChoiceField(
            choices=get_model_label_choices(),
            help_text=self._meta.model._meta.get_field("model_label").help_text,
        )


@admin.register(SupersetReplicatedTable)
class SupersetReplicatedTableAdmin(admin.ModelAdmin):
    form = SupersetReplicatedTableForm
    list_display = ["model_label", "config_synced_at", "config_sync_error_short"]
    readonly_fields = ["config_synced_at", "config_sync_error"]
    ordering = ["model_label"]

    @admin.display(description="Last sync error")
    def config_sync_error_short(self, obj):
        if not obj.config_sync_error:
            return ""
        return obj.config_sync_error[:80] + ("…" if len(obj.config_sync_error) > 80 else "")

    def has_module_permission(self, request):
        return request.user.is_active and request.user.is_superuser

    def get_fields(self, request, obj=None):
        if obj is None:
            return ["model_label"]
        return ["model_label", "config_synced_at", "config_sync_error"]

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        self._sync_and_report(request)

    def delete_model(self, request, obj):
        if SupersetReplicatedTable.objects.exclude(pk=obj.pk).count() == 0:
            messages.error(
                request,
                "Cannot delete the last replicated table — Postgres rejects empty publications. "
                "Add another table first, or use the management command to tear down replication.",
            )
            return
        super().delete_model(request, obj)
        self._sync_and_report(request)

    def delete_queryset(self, request, queryset):
        remaining = SupersetReplicatedTable.objects.exclude(pk__in=queryset.values("pk")).count()
        if remaining == 0:
            messages.error(
                request,
                "Cannot delete every replicated table — Postgres rejects empty publications. "
                "Keep at least one row, or use the management command to tear down replication.",
            )
            return
        super().delete_queryset(request, queryset)
        self._sync_and_report(request)

    def _sync_and_report(self, request):
        result = sync_replicated_tables()
        if result.ok:
            messages.success(request, "Replication publication and subscription updated.")
            return
        for error in result.errors:
            messages.error(request, error)
        messages.warning(
            request,
            "The model row was saved but Postgres is out of sync. "
            "Run `manage.py setup_logical_replication` to recover.",
        )
