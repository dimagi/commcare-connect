from django.db.models import DateTimeField, ExpressionWrapper, F, FloatField
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Cast, Extract


def get_visit_map_queryset(base_queryset):
    return (
        base_queryset.annotate(
            username_connectid=F("form_json__metadata__username"),
            deliver_unit_name=F("deliver_unit__name"),
            days_since_opp_start=ExpressionWrapper(
                Extract(F("visit_date") - F("opportunity__start_date"), "day"), output_field=FloatField()
            ),
            timestart_str=KeyTextTransform("timeStart", KeyTextTransform("metadata", F("form_json"))),
            timeend_str=KeyTextTransform("timeEnd", KeyTextTransform("metadata", F("form_json"))),
            visit_duration=ExpressionWrapper(
                Extract(Cast("timeend_str", DateTimeField()) - Cast("timestart_str", DateTimeField()), "epoch") / 60,
                output_field=FloatField(),
            ),
            location_str=KeyTextTransform("location", KeyTextTransform("metadata", F("form_json"))),
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
            "days_since_opp_start",
            "entity_id",
            "status",
            "flagged",
            "flag_reason",
            "reason",
            "timestart_str",
            "timeend_str",
            "visit_duration",
            "location_str",
        )
    )
