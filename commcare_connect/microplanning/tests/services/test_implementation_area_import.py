import io
from unittest import mock

import pytest
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile

from commcare_connect.microplanning.models import ImplementationArea, WorkArea
from commcare_connect.microplanning.services.implementation_area_import import (
    get_implementation_area_import_cache_key,
    import_implementation_areas,
    import_uploaded_implementation_areas,
    start_implementation_area_import,
)
from commcare_connect.microplanning.services.work_area_import import import_work_areas
from commcare_connect.microplanning.tests.factories import ImplementationAreaFactory

WA_BASE_HEADER = "Area Slug,Ward,Centroid,Boundary,Building Count,Expected Visit Count,Target Population,LGA,State"
WA_ROW = 'area-{n},Ward1,77.1 28.6,"POLYGON((77 28,78 28,78 29,77 29,77 28))",5,6,7,LGA1,State1'
IA_HEADER = "Implementation Area Name,Centroid,Boundary"
IA_POLY = "POLYGON((77 28,78 28,78 29,77 29,77 28))"


@pytest.mark.django_db
def test_implementation_area_import_backlinks_existing_work_areas(opportunity):
    # Work Areas uploaded first, before any Implementation Area exists: FK stays null, name kept.
    wa_content = io.StringIO(f"{WA_BASE_HEADER},Implementation Area\n" + WA_ROW.format(n=1) + ",Ward North\n")
    assert import_work_areas(opportunity_id=opportunity.id, csv_source=wa_content) == {"created": 1}
    assert WorkArea.objects.get(slug="area-1").implementation_area_id is None

    # Uploading the matching Implementation Area afterwards links the existing work area.
    ia_content = io.StringIO(f'{IA_HEADER}\nWard North,77.1 28.6,"{IA_POLY}"\n')
    assert import_implementation_areas(opportunity_id=opportunity.id, csv_source=ia_content) == {"created": 1}

    area = ImplementationArea.objects.get(opportunity=opportunity, name="Ward North")
    assert WorkArea.objects.get(slug="area-1").implementation_area_id == area.id


@pytest.mark.django_db
def test_implementation_area_import_does_not_link_unrelated_work_areas(opportunity):
    wa_content = io.StringIO(f"{WA_BASE_HEADER},Implementation Area\n" + WA_ROW.format(n=1) + ",Ward North\n")
    import_work_areas(opportunity_id=opportunity.id, csv_source=wa_content)

    ia_content = io.StringIO(f'{IA_HEADER}\nWard South,77.1 28.6,"{IA_POLY}"\n')
    import_implementation_areas(opportunity_id=opportunity.id, csv_source=ia_content)

    assert WorkArea.objects.get(slug="area-1").implementation_area_id is None


@pytest.mark.django_db
def test_implementation_area_import_success(opportunity):
    content = io.StringIO(f'{IA_HEADER}\nWard North,77.1 28.6,"{IA_POLY}"\nWard South,77.2 28.7,"{IA_POLY}"\n')
    result = import_implementation_areas(opportunity_id=opportunity.id, csv_source=content)
    assert result == {"created": 2}
    assert ImplementationArea.objects.filter(opportunity_id=opportunity.id).count() == 2


@pytest.mark.django_db
@pytest.mark.parametrize(
    "row,expected_error",
    [
        (f',77.1 28.6,"{IA_POLY}"', "Implementation Area Name is required and should be unique."),
        (f'Ward,bad-centroid,"{IA_POLY}"', "Centroid must be in 'lon lat' format"),
        ("Ward,77.1 28.6,NOT_WKT", "Invalid WKT format for Boundary(Polygon)."),
    ],
)
def test_implementation_area_row_validations(opportunity, row, expected_error):
    content = io.StringIO(f"{IA_HEADER}\n{row}\n")
    result = import_implementation_areas(opportunity_id=opportunity.id, csv_source=content)
    assert expected_error in result["errors"]
    assert result["errors"][expected_error] == [2]


@pytest.mark.django_db
def test_implementation_area_duplicate_name_in_file(opportunity):
    content = io.StringIO(f'{IA_HEADER}\nWard,77.1 28.6,"{IA_POLY}"\nWard,77.2 28.7,"{IA_POLY}"\n')
    result = import_implementation_areas(opportunity_id=opportunity.id, csv_source=content)
    assert "Duplicate Implementation Area Name in file" in result["errors"]


@pytest.mark.django_db
def test_implementation_area_name_that_already_exists(opportunity):
    ImplementationAreaFactory(opportunity=opportunity, name="Ward")
    content = io.StringIO(f'{IA_HEADER}\nWard,77.1 28.6,"{IA_POLY}"\nOther,77.2 28.7,"{IA_POLY}"\n')
    result = import_implementation_areas(opportunity_id=opportunity.id, csv_source=content)
    assert result == {"errors": {"Implementation Area Name already exists for this opportunity": [2]}}
    assert ImplementationArea.objects.filter(opportunity=opportunity).count() == 1


@pytest.mark.django_db
def test_implementation_area_short_row_reports_boundary_error(opportunity):
    # A row with fewer fields than headers leaves boundary as None; it must surface as a
    # clean per-row validation error, not crash the import with an AttributeError.
    content = io.StringIO(f"{IA_HEADER}\nWard,77.1 28.6\n")
    result = import_implementation_areas(opportunity_id=opportunity.id, csv_source=content)
    assert "Invalid WKT format for Boundary(Polygon)." in result["errors"]


@pytest.mark.django_db
def test_implementation_area_missing_columns(opportunity):
    content = io.StringIO("Implementation Area Name,Centroid\nWard,77.1 28.6\n")
    result = import_implementation_areas(opportunity_id=opportunity.id, csv_source=content)
    assert any("Missing columns" in msg for msg in result["errors"])


@pytest.mark.django_db
def test_import_uploaded_implementation_areas_refuses_when_areas_exist(opportunity):
    ImplementationAreaFactory(opportunity=opportunity)
    csv_source = io.StringIO(f'{IA_HEADER}\nWard North,77.1 28.6,"{IA_POLY}"\n')

    result = import_uploaded_implementation_areas(opportunity_id=opportunity.id, csv_source=csv_source)

    assert result == {"errors": {"Implementation Areas already exist for this opportunity": [0]}}
    assert ImplementationArea.objects.filter(opportunity=opportunity).count() == 1


@pytest.mark.django_db
class TestStartUpload:
    @pytest.fixture(autouse=True)
    def clear_lock(self, opportunity):
        cache.delete(get_implementation_area_import_cache_key(opportunity.id))
        yield
        cache.delete(get_implementation_area_import_cache_key(opportunity.id))

    @mock.patch("commcare_connect.microplanning.tasks.import_implementation_areas_task.delay")
    def test_stores_the_file_queues_the_import_and_takes_the_lock(self, delay, opportunity):
        delay.return_value.id = "task-1"

        task_id = start_implementation_area_import(
            opportunity=opportunity, csv_file=SimpleUploadedFile("areas.csv", b"a,b")
        )

        assert task_id == "task-1"
        opp_id, file_name = delay.call_args.args
        assert opp_id == opportunity.id
        with default_storage.open(file_name) as stored:
            assert stored.read() == b"a,b"
        default_storage.delete(file_name)
        assert cache.get(get_implementation_area_import_cache_key(opportunity.id)) == "task-1"

    @mock.patch("commcare_connect.microplanning.tasks.import_implementation_areas_task.delay")
    def test_refuses_when_areas_exist(self, delay, opportunity):
        ImplementationAreaFactory(opportunity=opportunity)

        with pytest.raises(ValidationError, match="Implementation Areas already exist"):
            start_implementation_area_import(opportunity=opportunity, csv_file=SimpleUploadedFile("areas.csv", b"a,b"))
        delay.assert_not_called()

    @mock.patch("commcare_connect.microplanning.tasks.import_implementation_areas_task.delay")
    def test_refuses_while_an_import_runs(self, delay, opportunity):
        cache.set(get_implementation_area_import_cache_key(opportunity.id), "running")

        with pytest.raises(ValidationError, match="already in progress"):
            start_implementation_area_import(opportunity=opportunity, csv_file=SimpleUploadedFile("areas.csv", b"a,b"))
        delay.assert_not_called()
