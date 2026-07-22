import pytest
from django.contrib.gis.geos import Point

from commcare_connect.microplanning.models import SRID
from commcare_connect.microplanning.serializers import WORK_AREA_CASE_TYPE, WorkAreaCaseSerializer
from commcare_connect.microplanning.tests.factories import WorkAreaFactory, WorkAreaGroupFactory


@pytest.mark.django_db
def test_work_area_case_serializer():
    group = WorkAreaGroupFactory(name="group-a")
    work_area = WorkAreaFactory(
        slug="my-area",
        ward="ward-x",
        work_area_group=group,
        building_count=5,
        expected_visit_count=10,
        case_properties={
            "lga": "LGA1",
            "state": "State1",
        },
        centroid=Point(77, 29, srid=SRID),
    )
    group.update_centroid()

    data = WorkAreaCaseSerializer(work_area).data

    assert data == {
        "case_name": "my-area",
        "case_type": WORK_AREA_CASE_TYPE,
        "external_id": str(work_area.id),
        "owner_id": None,
        "properties": {
            "bounding_box": "28.00000 77.00000 28.00000 78.00000 29.00000"
            " 78.00000 29.00000 77.00000 28.00000 77.00000",
            "bounding_box_wkt": str(work_area.boundary),
            "building_count": "5",
            "centroid": "29.00000 77.00000",
            "centroid_wkt": str(work_area.centroid),
            "expected_visit_count": "10",
            "wa_status": work_area.status,
            "ward": "ward-x",
            "work_area_group": "group-a",
            "work_area_group_id": str(group.id),
            "lga": "LGA1",
            "state": "State1",
            "wag_centroid": "28.50000 77.50000",
        },
    }
