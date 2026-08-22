from django.contrib import admin
from django.core.exceptions import ObjectDoesNotExist

UNKNOWN = "—"


class AuditEventAdmin(admin.ModelAdmin):
    """Base admin for pghistory event models.

    Event rows are written by database triggers, so they are shown read-only: editing or
    deleting them from the admin would falsify the record it exists to preserve. Viewing is
    still governed by the model's normal `view_*` permission.
    """

    date_hierarchy = "pgh_created_at"
    ordering = ("-pgh_id",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("pgh_context")

    @admin.display(description="Changed by", ordering="pgh_context__metadata__username")
    def changed_by(self, obj):
        """Read the actor off the pghistory context.

        `CustomPGHistoryMiddleware` stores username and email here rather than a user
        foreign key, so the record survives the user being deleted. Changes made outside a
        request — a migration, a shell, a management command — carry no context at all.
        """
        metadata = getattr(obj.pgh_context, "metadata", None) or {}
        return metadata.get("username") or UNKNOWN

    @admin.display(description="Email", ordering="pgh_context__metadata__user_email")
    def changed_by_email(self, obj):
        metadata = getattr(obj.pgh_context, "metadata", None) or {}
        return metadata.get("user_email") or UNKNOWN


def audited_object(field_name, description):
    """Build a display method for an event's `pgh_obj`-style pointer.

    These relations are declared with `db_constraint=False`, so the row they point at can be
    deleted while the event survives — which is the point of an audit trail, but means the
    attribute can raise instead of returning a value.
    """

    @admin.display(description=description, ordering=field_name)
    def display(self, obj):
        try:
            related = getattr(obj, field_name)
        except ObjectDoesNotExist:
            related = None
        return related or UNKNOWN

    return display
