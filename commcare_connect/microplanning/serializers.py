from rest_framework import serializers

from commcare_connect.microplanning.const import WORK_AREA_CASE_TYPE
from commcare_connect.microplanning.models import WorkArea


class WorkAreaCaseSerializer(serializers.ModelSerializer):
    case_name = serializers.CharField(source="slug")
    case_type = serializers.CharField(default=WORK_AREA_CASE_TYPE, read_only=True)
    external_id = serializers.CharField(source="id")
    owner_id = serializers.CharField(default=None, allow_null=True)
    properties = serializers.SerializerMethodField()

    class Meta:
        model = WorkArea
        fields = [
            "case_name",
            "case_type",
            "external_id",
            "owner_id",  # Required when creating new case in HQ, optional when doing update
            "properties",
        ]

    def get_properties(self, obj) -> dict:
        return {
            "bounding_box": str(obj.boundary) if obj.boundary else "",
            "building_count": str(obj.building_count),
            "centroid": str(obj.centroid) if obj.centroid else "",
            "expected_visit_count": str(obj.expected_visit_count),
            "wa_status": obj.status,
            "ward": obj.ward,
            "work_area_group": getattr(obj.work_area_group, "name", ""),
            "max_wag": str(obj.extra_case_properties.get("max_wag", "")),
            "wag_serial_number": str(obj.extra_case_properties.get("wag_serial_number", "")),
            "lga": str(obj.extra_case_properties.get("lga", "")),
            "state": str(obj.extra_case_properties.get("state", "")),
        }
