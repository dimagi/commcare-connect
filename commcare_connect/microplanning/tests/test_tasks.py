import csv
import io
from unittest import mock

import pytest
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

from commcare_connect.microplanning.const import DEFAULT_BUILDING_COUNT
from commcare_connect.microplanning.models import WorkArea, WorkAreaGroup
from commcare_connect.microplanning.tasks import (
    WorkAreaCSVImporter,
    cluster_work_areas_task,
    import_work_areas_task,
    send_work_area_assignment_notification,
)
from commcare_connect.microplanning.tests.factories import WorkAreaFactory, WorkAreaGroupFactory
from commcare_connect.opportunity.tests.factories import OpportunityAccessFactory


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

    def test_case_insensitive_headers(self, opportunity):
        headers = [
            "area slug",
            "WARD",
            "Centroid",
            "boundary",
            "Building count",
            "expected VISIT count",
            "target population",
            "lga",
            "STATE",
        ]
        row = [
            "area-case",
            "ward",
            self.CENTROID,
            self.POLYGON,
            5,
            "6",
            "100",
            "LGA1",
            "State1",
        ]

        csv_data = self.build_csv([row], headers=headers)
        result = WorkAreaCSVImporter(opportunity.id, csv_data).run()

        assert result["created"] == 1
        assert WorkArea.objects.filter(slug="area-case", target_population=100).exists()

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

    def build_row(self, slug, group_name=None):
        row = [
            slug,
            "ward",
            self.CENTROID,
            self.POLYGON,
            5,
            "6",
            "100",
            "LGA1",
            "State1",
        ]
        if group_name is not None:
            row.append(group_name)
        return row

    def build_csv_with_groups(self, rows):
        headers = self.HEADERS + [WorkAreaCSVImporter.GROUP_NAME_HEADER]
        return self.build_csv(rows, headers=headers)

    def test_import_assigns_work_area_groups_when_all_rows_have_one(self, opportunity):
        rows = [
            self.build_row("area-1", "group-a"),
            self.build_row("area-2", "group-a"),
            self.build_row("area-3", "group-b"),
        ]
        result = WorkAreaCSVImporter(opportunity.id, self.build_csv_with_groups(rows)).run()

        assert result["created"] == 3
        groups = {g.name: g for g in WorkAreaGroup.objects.filter(opportunity=opportunity)}
        assert set(groups) == {"group-a", "group-b"}
        assert WorkArea.objects.get(slug="area-1").work_area_group == groups["group-a"]
        assert WorkArea.objects.get(slug="area-2").work_area_group == groups["group-a"]
        assert WorkArea.objects.get(slug="area-3").work_area_group == groups["group-b"]
        # centroid should be computed for the newly created groups
        assert groups["group-a"].centroid is not None
        assert groups["group-b"].centroid is not None

    def test_import_without_group_column_leaves_work_areas_ungrouped(self, opportunity):
        rows = [self.build_row("area-1"), self.build_row("area-2")]
        result = WorkAreaCSVImporter(opportunity.id, self.build_csv(rows)).run()

        assert result["created"] == 2
        assert not WorkAreaGroup.objects.filter(opportunity=opportunity).exists()
        assert WorkArea.objects.get(slug="area-1").work_area_group is None

    def test_import_errors_when_only_some_rows_have_a_group(self, opportunity):
        rows = [
            self.build_row("area-1", "group-a"),
            self.build_row("area-2", ""),
        ]
        result = WorkAreaCSVImporter(opportunity.id, self.build_csv_with_groups(rows)).run()

        assert "errors" in result
        error_keys = " ".join(result["errors"].keys()).lower()
        assert "work area group name" in error_keys
        assert WorkArea.objects.count() == 0
        assert not WorkAreaGroup.objects.filter(opportunity=opportunity).exists()

    def test_import_reuses_existing_work_area_group_with_same_name(self, opportunity):
        existing_group = WorkAreaGroupFactory(opportunity=opportunity, name="group-a", ward="other-ward")
        rows = [self.build_row("area-1", "group-a")]
        result = WorkAreaCSVImporter(opportunity.id, self.build_csv_with_groups(rows)).run()

        assert result["created"] == 1
        assert WorkAreaGroup.objects.filter(opportunity=opportunity, name="group-a").count() == 1
        assert WorkArea.objects.get(slug="area-1").work_area_group == existing_group


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


@mock.patch("commcare_connect.microplanning.tasks.cache")
@mock.patch("commcare_connect.microplanning.tasks.WorkAreaGrouper")
def test_cluster_work_areas_task_forwards_max_buildings(mock_grouper, mock_cache):
    cluster_work_areas_task(opp_id=1, max_buildings=250)

    mock_grouper.assert_called_once_with(1, max_buildings=250)
    mock_grouper.return_value.cluster_work_areas.assert_called_once()


@mock.patch("commcare_connect.microplanning.tasks.cache")
@mock.patch("commcare_connect.microplanning.tasks.WorkAreaGrouper")
def test_cluster_work_areas_task_defaults_max_buildings(mock_grouper, mock_cache):

    cluster_work_areas_task(opp_id=1)

    mock_grouper.assert_called_once_with(1, max_buildings=DEFAULT_BUILDING_COUNT)
