from rest_framework import serializers

from commcare_connect.microplanning.models import WorkArea

WORK_AREA_CASE_TYPE = "work-area"


class WorkAreaCaseSerializer(serializers.ModelSerializer):
    case_name = serializers.CharField(source="slug")
    case_type = serializers.SerializerMethodField()
    external_id = serializers.CharField(source="id")
    properties = serializers.SerializerMethodField()

    class Meta:
        model = WorkArea
        fields = [
            "case_name",
            "case_type",
            "external_id",
            "properties",
        ]

    def get_case_type(self, obj) -> str:
        return WORK_AREA_CASE_TYPE

    def get_properties(self, obj) -> dict:
        return {
            "bounding_box": str(obj.boundary) if obj.boundary else "",
            "building_count": str(obj.building_count),
            "centroid": str(obj.centroid) if obj.centroid else "",
            "expected_visit_count": str(obj.expected_visit_count),
            "wa_status": obj.status,
            "ward": str(obj.ward),
            "work_area_group": obj.work_area_group.name if obj.work_area_group else "",
        }
