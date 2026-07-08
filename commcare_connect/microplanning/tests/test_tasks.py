import csv
import io
from unittest import mock

import pytest
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

from commcare_connect.microplanning.models import ImplementationArea, WorkArea
from commcare_connect.microplanning.tasks import (
    ImplementationAreaCSVImporter,
    WorkAreaCSVImporter,
    import_work_areas_task,
    send_work_area_assignment_notification,
)
from commcare_connect.microplanning.tests.factories import ImplementationAreaFactory, WorkAreaFactory
from commcare_connect.opportunity.tests.factories import OpportunityAccessFactory

WA_BASE_HEADER = "Area Slug,Ward,Centroid,Boundary,Building Count,Expected Visit Count,Target Population,LGA,State"
WA_ROW = 'area-{n},Ward1,77.1 28.6,"POLYGON((77 28,78 28,78 29,77 29,77 28))",5,6,7,LGA1,State1'


@pytest.fixture
def work_area(opportunity):
    return WorkAreaFactory(opportunity=opportunity)


@pytest.mark.django_db
class TestWorkAreaCSVImporter:
    CENTROID = "77.1 28.6"
    POLYGON = "POLYGON((77 28, 78 28, 78 29, 77 29, 77 28))"
    HEADERS = [
        "Area Slug",
        "Ward",
        "Centroid",
        "Boundary",
        "Building Count",
        "Expected Visit Count",
        "Target Population",
        "LGA",
        "State",
    ]

    def build_csv(self, rows, headers=None):
        headers = headers or self.HEADERS
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(headers)
        for row in rows:
            writer.writerow(row)
        output.seek(0)
        return output

    def test_successful_import(self, opportunity):
        csv = self.build_csv(
            [
                [
                    "area-1",
                    "ward",
                    self.CENTROID,
                    self.POLYGON,
                    5,
                    "6",
                    "100",
                    "LGA1",
                    "State1",
                ]
            ]
        )
        result = WorkAreaCSVImporter(opportunity.id, csv).run()
        assert result["created"] == 1
        assert WorkArea.objects.filter(slug="area-1").exists()

    @pytest.mark.parametrize(
        "row, expected_msg",
        [
            # empty slug
            (["", "test-ward", CENTROID, POLYGON, "1", "1"], "slug"),
            # invalid boundry
            (
                [
                    "test-slug",
                    "test-ward",
                    CENTROID,
                    "BAD Boundry",
                    "1",
                    "1",
                ],
                "boundary(polygon)",
            ),
            # invalid centroid
            (
                [
                    "test-slug",
                    "test-ward",
                    "1,2",
                    POLYGON,
                    "1",
                    "1",
                ],
                "centroid",
            ),
            # invalid visit count and building count
            (["slug", "test-ward", CENTROID, POLYGON, "abc", "-2", ""], "positive integers"),
        ],
    )
    def test_row_validations(self, opportunity, row, expected_msg):
        csv = self.build_csv([row])

        result = WorkAreaCSVImporter(opportunity.id, csv).run()
        assert "errors" in result
        error_keys = " ".join(result["errors"].keys()).lower()
        assert expected_msg.lower() in error_keys
        assert WorkArea.objects.count() == 0

    def test_duplicate_slug_in_file(self, opportunity):
        rows = [
            [
                "dup",
                "test-ward",
                self.CENTROID,
                self.POLYGON,
                "1",
                "1",
            ],
            [
                "dup",
                "test-ward",
                self.CENTROID,
                self.POLYGON,
                "1",
                "1",
            ],
        ]
        result = WorkAreaCSVImporter(opportunity.id, self.build_csv(rows)).run()
        assert "errors" in result
        assert "duplicate" in " ".join(result["errors"].keys()).lower()

    def test_slug_exists_in_db(self, opportunity, work_area):
        csv = self.build_csv([[work_area.slug, self.CENTROID, self.POLYGON, "1", "1"]])
        result = WorkAreaCSVImporter(opportunity.id, csv).run()
        assert "errors" in result
        assert "exists" in " ".join(result["errors"].keys()).lower()

    def test_random_column_order(self, opportunity):
        headers = [
            "Expected Visit Count",
            "Boundary",
            "Area Slug",
            "Centroid",
            "Ward",
            "Building Count",
            "Target Population",
            "State",
            "LGA",
        ]

        row = [
            "10",
            self.POLYGON,
            "area-random",
            self.CENTROID,
            "ward-1",
            "5",
            "50",
            "State2",
            "LGA2",
        ]

        csv_data = self.build_csv([row], headers=headers)
        result = WorkAreaCSVImporter(opportunity.id, csv_data).run()

        assert result["created"] == 1

    def test_missing_extra_properties(self, opportunity):
        row = [
            "area-1",
            "ward",
            self.CENTROID,
            self.POLYGON,
            5,
            "6",
        ]
        csv = self.build_csv([row])
        result = WorkAreaCSVImporter(opportunity.id, csv).run()
        assert "errors" in result
        error_keys = " ".join(result["errors"].keys()).lower()
        expected_error = "Missing values for properties: lga, state"
        assert expected_error.lower() in error_keys


@pytest.mark.django_db
def test_import_work_areas_task_bom_csv(opportunity):
    CENTROID = "77.1 28.6"
    POLYGON = "POLYGON((77 28, 78 28, 78 29, 77 29, 77 28))"
    headers = "Area Slug,Ward,Centroid,Boundary,Building Count,Expected Visit Count,Target Population,LGA,State\r\n"
    row = f'area-bom,ward,{CENTROID},"{POLYGON}",5,6,100,LGA1,State1\r\n'
    bom_bytes = ("\ufeff" + headers + row).encode("utf-8")

    file_name = "test-bom-upload.csv"
    default_storage.save(file_name, ContentFile(bom_bytes))
    result = import_work_areas_task.apply(args=[opportunity.id, file_name]).get()

    assert result["created"] == 1
    assert WorkArea.objects.filter(slug="area-bom").exists()


@pytest.mark.django_db
def test_send_work_area_assignment_notification(opportunity):
    access = OpportunityAccessFactory(opportunity=opportunity)
    with mock.patch("commcare_connect.microplanning.tasks.send_message") as send_message_mock:
        send_work_area_assignment_notification(access.pk)

    send_message_mock.assert_called_once()
    message = send_message_mock.call_args.args[0]
    assert message.usernames == [access.user.username]
    assert message.data["key"] == "work_area_assignment"
    assert message.data["opportunity_uuid"] == str(opportunity.opportunity_id)
    assert message.data["title"]
    assert message.data["body"]


@pytest.mark.django_db
def test_work_area_import_matches_implementation_area_by_name(opportunity):
    ia = ImplementationAreaFactory(opportunity=opportunity, name="Ward North")
    content = io.StringIO(f"{WA_BASE_HEADER},Implementation Area\n" + WA_ROW.format(n=1) + ",Ward North\n")
    result = WorkAreaCSVImporter(opportunity.id, content).run()
    assert result == {"created": 1}
    assert WorkArea.objects.get(slug="area-1").implementation_area_id == ia.id


@pytest.mark.django_db
def test_work_area_import_unmatched_implementation_area_is_null(opportunity):
    content = io.StringIO(f"{WA_BASE_HEADER},Implementation Area\n" + WA_ROW.format(n=1) + ",Nonexistent\n")
    result = WorkAreaCSVImporter(opportunity.id, content).run()
    assert result == {"created": 1}
    assert WorkArea.objects.get(slug="area-1").implementation_area_id is None


@pytest.mark.django_db
def test_work_area_import_without_implementation_area_column(opportunity):
    content = io.StringIO(f"{WA_BASE_HEADER}\n" + WA_ROW.format(n=1) + "\n")
    result = WorkAreaCSVImporter(opportunity.id, content).run()
    assert result == {"created": 1}
    assert WorkArea.objects.get(slug="area-1").implementation_area_id is None


IA_HEADER = "Implementation Area Name,Centroid,Boundary"
IA_POLY = "POLYGON((77 28,78 28,78 29,77 29,77 28))"


@pytest.mark.django_db
def test_implementation_area_import_success(opportunity):
    content = io.StringIO(f'{IA_HEADER}\nWard North,77.1 28.6,"{IA_POLY}"\nWard South,77.2 28.7,"{IA_POLY}"\n')
    result = ImplementationAreaCSVImporter(opportunity.id, content).run()
    assert result == {"created": 2}
    assert ImplementationArea.objects.filter(opportunity_id=opportunity.id).count() == 2


@pytest.mark.django_db
@pytest.mark.parametrize(
    "row,expected_error",
    [
        (f",77.1 28.6,{IA_POLY}", "Implementation Area Name is required and should be unique."),
        ("Ward,bad-centroid," + IA_POLY, "Centroid must be in 'lon lat' format"),
        ("Ward,77.1 28.6,NOT_WKT", "Invalid WKT format for Boundary(Polygon)."),
    ],
)
def test_implementation_area_row_validations(opportunity, row, expected_error):
    content = io.StringIO(f"{IA_HEADER}\n{row}\n")
    result = ImplementationAreaCSVImporter(opportunity.id, content).run()
    assert expected_error in result["errors"]
    assert result["errors"][expected_error] == [2]


@pytest.mark.django_db
def test_implementation_area_duplicate_name_in_file(opportunity):
    content = io.StringIO(f"{IA_HEADER}\nWard,77.1 28.6,{IA_POLY}\nWard,77.2 28.7,{IA_POLY}\n")
    result = ImplementationAreaCSVImporter(opportunity.id, content).run()
    assert "Duplicate Implementation Area Name in file" in result["errors"]


@pytest.mark.django_db
def test_implementation_area_missing_columns(opportunity):
    content = io.StringIO("Implementation Area Name,Centroid\nWard,77.1 28.6\n")
    result = ImplementationAreaCSVImporter(opportunity.id, content).run()
    assert any("Missing columns" in msg for msg in result["errors"])
