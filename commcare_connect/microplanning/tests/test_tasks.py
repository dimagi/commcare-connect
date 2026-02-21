import csv
import io
from ctypes.wintypes import POINT

import pytest

from commcare_connect.microplanning.models import WorkArea
from commcare_connect.microplanning.tasks import WorkAreaCSVImporter
from commcare_connect.microplanning.tests.factories import WorkAreaFactory


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
            (["", CENTROID, POLYGON, "1", "1"], "slug"),
            # invalid boundry
            (
                [
                    "test-slug",
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
                    "test-slug" "1,2",
                    POLYGON,
                    "1",
                    "1",
                ],
                "centroid",
            ),
            # invalid visit count and building count
            (["slug", POINT, POLYGON, "abc", "-2", ""], "postive integers"),
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
                self.CENTROID,
                self.POLYGON,
                "1",
                "1",
            ],
            [
                "dup",
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
        ]

        row = [
            "10",
            self.POLYGON,
            "area-random",
            self.CENTROID,
            "ward-1",
            "5",
        ]

        csv_data = self.build_csv([row], headers=headers)
        result = WorkAreaCSVImporter(opportunity.id, csv_data).run()

        assert result["created"] == 1
