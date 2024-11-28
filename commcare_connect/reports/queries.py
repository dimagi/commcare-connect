from django.db.models import F
from django.db.models.fields.json import KT


def get_visit_map_queryset(base_queryset):
    return (
        base_queryset.annotate(
            deliver_unit_name=F("deliver_unit__name"),
            username_connectid=KT("form_json__metadata__username"),
            timestart_str=KT("form_json__metadata__timeStart"),
            timeend_str=KT("form_json__metadata__timeEnd"),
            location_str=KT("form_json__metadata__location"),
        )
        .select_related("deliver_unit", "opportunity", "opportunity__delivery_type", "opportunity__organization")
        .values(
            "opportunity_id",
            "opportunity__delivery_type__name",
            "opportunity__delivery_type__slug",
            "opportunity__organization__slug",
            "opportunity__organization__name",
            "xform_id",
            "visit_date",
            "username_connectid",
            "deliver_unit_name",
            "entity_id",
            "status",
            "flagged",
            "flag_reason",
            "reason",
            "timestart_str",
            "timeend_str",
            "location_str",
        )
    )
