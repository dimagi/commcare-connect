import pytest

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
    )

    data = WorkAreaCaseSerializer(work_area).data
    assert data == {
        "case_name": "my-area",
        "case_type": WORK_AREA_CASE_TYPE,
        "external_id": str(work_area.id),
        "properties": {
            "bounding_box": str(work_area.boundary),
            "building_count": "5",
            "centroid": str(work_area.centroid),
            "expected_visit_count": "10",
            "wa_status": work_area.status,
            "ward": "ward-x",
            "work_area_group": "group-a",
        },
    }
