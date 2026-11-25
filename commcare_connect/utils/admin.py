from django.contrib import admin
from django.core.exceptions import ObjectDoesNotExist
from django.utils.translation import gettext_lazy as _

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

    @admin.display(description=_("Changed by"), ordering="pgh_context__metadata__username")
    def changed_by(self, obj):
        """Read the actor off the pghistory context.

        `CustomPGHistoryMiddleware` stores username and email here rather than a user
        foreign key, so the record survives the user being deleted. Changes made outside a
        request — a migration, a shell, a management command — carry no context at all.
        """
        metadata = getattr(obj.pgh_context, "metadata", None) or {}
        return metadata.get("username") or UNKNOWN

    @admin.display(description=_("Email"), ordering="pgh_context__metadata__user_email")
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


class ChangedFieldsMixin:
    """Adds a "Changes" field to an event's page, listing the fields that differ from the record's previous event.

    Events are snapshots of the whole record, so a change is found by comparing an event with the
    one before it for the same record. A record's first event has nothing to compare against.
    """

    def get_readonly_fields(self, request, obj=None):
        return ("changes", *super().get_readonly_fields(request, obj))

    @admin.display(description=_("Changes"))
    def changes(self, obj):
        previous_event = (
            type(obj).objects.filter(pgh_obj_id=obj.pgh_obj_id, pgh_id__lt=obj.pgh_id).order_by("-pgh_id").first()
        )
        return changed_fields_summary(previous_event, obj) or UNKNOWN


def changed_fields_summary(previous_event, event):
    """Describes each tracked field whose value differs between two events, e.g. "Status: — → Active"."""
    if previous_event is None:
        return ""
    changes = []
    for field in _tracked_fields(event):
        # Compared on the stored values, not the displayed ones (choice labels, related records' names).
        if _stored_value(previous_event, field) != _stored_value(event, field):
            before, after = _event_value(previous_event, field), _event_value(event, field)
            changes.append(f"{field.verbose_name.capitalize()}: {before} → {after}")
    return "; ".join(changes)


def _stored_value(event, field):
    """The field's stored value, with None and an empty string counted as the same empty value."""
    value = getattr(event, field.attname)
    return None if value == "" else value


def _tracked_fields(event):
    tracked_pk = event.pgh_tracked_model._meta.pk.attname
    return [
        field
        for field in event._meta.concrete_fields
        if not field.name.startswith("pgh_") and field.attname != tracked_pk
    ]


def _event_value(event, field):
    value = getattr(event, field.attname)
    if value in (None, ""):
        return UNKNOWN
    if field.is_relation:
        try:
            value = getattr(event, field.name)
        except ObjectDoesNotExist:
            pass
    elif field.choices:
        value = dict(field.flatchoices).get(value, value)
    elif isinstance(value, bool):
        value = _("Yes") if value else _("No")
    return str(value)
