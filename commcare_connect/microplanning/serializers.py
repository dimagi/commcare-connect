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

    def get_properties(self, obj: WorkArea) -> dict:
        centroid_lat_lon = ""
        if obj.centroid:
            centroid_lat_lon = _coords_to_lat_lon_string(obj.centroid.coords)
        bounding_box_lat_lon = ""
        if obj.boundary:
            lat_lon_strings = [_coords_to_lat_lon_string(coords) for coords in list(obj.boundary.shell.coords)]
            bounding_box_lat_lon = " ".join(lat_lon_strings)

        wag_centroid = ""
        if obj.work_area_group and obj.work_area_group.centroid:
            wag_centroid = _coords_to_lat_lon_string(obj.work_area_group.centroid)

        return {
            "bounding_box": bounding_box_lat_lon,
            "bounding_box_wkt": str(obj.boundary) if obj.boundary else "",
            "building_count": str(obj.building_count),
            "centroid": centroid_lat_lon,
            "centroid_wkt": str(obj.centroid) if obj.centroid else "",
            "expected_visit_count": str(obj.expected_visit_count),
            "wa_status": obj.status,
            "ward": obj.ward,
            "work_area_group": getattr(obj.work_area_group, "name", ""),
            "work_area_group_id": str(obj.work_area_group_id) if obj.work_area_group_id else "",
            "lga": str(obj.case_properties.get("lga", "")),
            "state": str(obj.case_properties.get("state", "")),
            "wag_centroid": wag_centroid,
        }


def _coords_to_lat_lon_string(coords: tuple[float, float]) -> str:
    lon, lat = coords
    return f"{lat:.5f} {lon:.5f}"
