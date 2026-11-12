import csv
import io
import itertools
from unittest import mock

import httpx
import pytest
from django.contrib.gis.geos import Point, Polygon
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import connection
from django.test.utils import CaptureQueriesContext
from kombu.exceptions import OperationalError

from commcare_connect.microplanning.const import DEFAULT_BUILDING_COUNT
from commcare_connect.microplanning.models import (
    SRID,
    ImplementationArea,
    OvertureRelease,
    WorkArea,
    WorkAreaGroup,
    WorkAreaStatus,
)
from commcare_connect.microplanning.tasks import (
    OVERTURE_CATALOG_URL,
    ImplementationAreaCSVImporter,
    WorkAreaCSVImporter,
    _overlap_percent,
    cluster_work_areas_task,
    import_work_areas_task,
    send_work_area_assignment_notification,
    sync_work_area_group_cases,
    update_overture_release,
)
from commcare_connect.microplanning.tests.factories import (
    ImplementationAreaFactory,
    WorkAreaFactory,
    WorkAreaGroupFactory,
    square,
)
from commcare_connect.opportunity.tests.factories import (
    OpportunityAccessFactory,
    OpportunityFactory,
    UserVisitFactory,
)
from commcare_connect.utils.commcarehq_api import CommCareHQAPIException

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

    # Each built row gets its own square, so rows in one file never overlap each other.
    _polygon_offsets = itertools.count()

    def build_row(self, slug, group_name=None, ward="ward", boundary=None):
        row = [
            slug,
            ward,
            self.CENTROID,
            (boundary or square(77 + 2 * next(self._polygon_offsets), 28)).wkt,
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
        assert WorkArea.objects.get(slug="area-2").work_area_group is None

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
        existing_group = WorkAreaGroupFactory(opportunity=opportunity, name="group-a", ward="ward")
        rows = [self.build_row("area-1", "group-a")]
        result = WorkAreaCSVImporter(opportunity.id, self.build_csv_with_groups(rows)).run()

        assert result["created"] == 1
        assert WorkAreaGroup.objects.filter(opportunity=opportunity, name="group-a").count() == 1
        assert WorkArea.objects.get(slug="area-1").work_area_group == existing_group

    def test_import_next_to_existing_work_area(self, opportunity):
        WorkAreaFactory(opportunity=opportunity, slug="existing", boundary=square(0, 0))
        rows = [self.build_row("area-1", boundary=square(1, 0))]  # shares an edge

        result = WorkAreaCSVImporter(opportunity.id, self.build_csv(rows)).run()

        assert result == {"created": 1}

    def test_overlap_with_existing_work_area_is_rejected(self, opportunity):
        existing = WorkAreaFactory(opportunity=opportunity, slug="existing", boundary=square(0, 0))
        rows = [
            self.build_row("area-1"),
            self.build_row("area-2", boundary=square(0.75, 0)),
        ]

        result = WorkAreaCSVImporter(opportunity.id, self.build_csv(rows)).run()

        assert result == {
            "errors": {
                "Work Area area-2 overlaps existing Work Area existing by 25.0%. "
                "Correct the boundary or remove the work area, then upload again.": [3]
            }
        }
        assert list(WorkArea.objects.filter(opportunity=opportunity)) == [existing]

    def test_overlap_within_the_file_is_rejected_once_naming_both_rows(self, opportunity):
        rows = [
            self.build_row("area-1", boundary=square(0, 0)),
            self.build_row("area-2"),
            self.build_row("area-3", boundary=square(0.5, 0)),
        ]

        result = WorkAreaCSVImporter(opportunity.id, self.build_csv(rows)).run()

        assert result == {
            "errors": {
                "Work Areas area-1 and area-3 overlap each other by 50.0%. "
                "Correct one boundary or remove one work area, then upload again.": [2, 4]
            }
        }
        assert not WorkArea.objects.filter(opportunity=opportunity).exists()

    def test_import_rejects_new_group_spanning_wards(self, opportunity):
        rows = [
            self.build_row("area-1", "group-a", ward="Dandi"),
            self.build_row("area-2", "group-b", ward="Nok"),
            self.build_row("area-3", "group-a", ward="Dabaza"),
            self.build_row("area-4", "group-a", ward="Todi"),
        ]
        result = WorkAreaCSVImporter(opportunity.id, self.build_csv_with_groups(rows)).run()

        assert result == {
            "errors": {
                "Work Area Group group-a is in ward Dandi, but these rows give ward Dabaza. "
                "Correct the ward, or use a different group name.": [4],
                "Work Area Group group-a is in ward Dandi, but these rows give ward Todi. "
                "Correct the ward, or use a different group name.": [5],
            }
        }
        assert not WorkArea.objects.filter(opportunity=opportunity).exists()
        assert not WorkAreaGroup.objects.filter(opportunity=opportunity).exists()

    def test_import_rejects_rows_in_another_ward_than_existing_group(self, opportunity):
        WorkAreaGroupFactory(opportunity=opportunity, name="group-a", ward="Dandi")
        rows = [
            self.build_row("area-1", "group-a", ward="Dandi"),
            self.build_row("area-2", "group-a", ward="Dabaza"),
            self.build_row("area-3", "group-a", ward="Dabaza"),
        ]
        result = WorkAreaCSVImporter(opportunity.id, self.build_csv_with_groups(rows)).run()

        assert result == {
            "errors": {
                "Work Area Group group-a is in ward Dandi, but these rows give ward Dabaza. "
                "Correct the ward, or use a different group name.": [3, 4]
            }
        }
        assert not WorkArea.objects.filter(opportunity=opportunity).exists()

    def test_overlap_over_the_limit_is_summed_up(self, opportunity):
        WorkAreaFactory(opportunity=opportunity, slug="existing", boundary=square(0, 0, 100))
        rows = [self.build_row(f"area-{n}", boundary=square(n, 1)) for n in range(52)]

        result = WorkAreaCSVImporter(opportunity.id, self.build_csv(rows)).run()

        assert len(result["errors"]) == 51
        assert list(result["errors"].items())[-1] == ("2 more overlaps not shown.", [1])

    def test_rejected_import_leaves_no_new_groups(self, opportunity):
        WorkAreaFactory(opportunity=opportunity, slug="existing", boundary=square(0, 0))
        rows = [self.build_row("area-1", "new-group", boundary=square(0, 0))]

        result = WorkAreaCSVImporter(opportunity.id, self.build_csv_with_groups(rows)).run()

        assert "errors" in result
        assert not WorkAreaGroup.objects.filter(opportunity=opportunity).exists()
        assert WorkArea.objects.filter(opportunity=opportunity).count() == 1

    def test_self_crossing_boundary_is_a_row_error(self, opportunity):
        bow_tie = Polygon(((0, 0), (1, 1), (1, 0), (0, 1), (0, 0)), srid=SRID)
        rows = [self.build_row("area-1"), self.build_row("area-2", boundary=bow_tie)]

        result = WorkAreaCSVImporter(opportunity.id, self.build_csv(rows)).run()

        assert result == {
            "errors": {
                "Boundary is not a valid polygon, e.g. its edges cross. Fix the boundary, then upload again.": [3]
            }
        }
        assert not WorkArea.objects.filter(opportunity=opportunity).exists()

    def test_import_locks_the_opportunity_before_reading_work_areas_or_groups(self, opportunity):
        csv = self.build_csv_with_groups([self.build_row("area-1", "group-a")])
        with CaptureQueriesContext(connection) as queries:
            WorkAreaCSVImporter(opportunity.id, csv).run()

        sqls = [query["sql"] for query in queries]
        lock = next(i for i, sql in enumerate(sqls) if "FOR NO KEY UPDATE" in sql)
        first_work_area_query = next(i for i, sql in enumerate(sqls) if '"microplanning_workarea"' in sql)
        first_group_query = next(i for i, sql in enumerate(sqls) if '"microplanning_workareagroup"' in sql)
        assert '"opportunity_opportunity"' in sqls[lock]
        assert lock < first_work_area_query
        assert lock < first_group_query

    def test_import_refreshes_statistics_between_insert_and_overlap_check(self, opportunity):
        with CaptureQueriesContext(connection) as queries:
            WorkAreaCSVImporter(opportunity.id, self.build_csv([self.build_row("area-1")])).run()

        sqls = [query["sql"] for query in queries]
        insert = next(i for i, sql in enumerate(sqls) if sql.startswith('INSERT INTO "microplanning_workarea"'))
        analyze = next(i for i, sql in enumerate(sqls) if sql.startswith("ANALYZE"))
        overlap_check = next(i for i, sql in enumerate(sqls) if "ST_Intersection" in sql)
        assert sqls[analyze] == 'ANALYZE "microplanning_workarea" ("opportunity_id")'
        assert insert < analyze < overlap_check

    def test_import_validates_rows_before_locking_the_opportunity(self, opportunity):
        importer = WorkAreaCSVImporter(opportunity.id, self.build_csv([self.build_row("")]))
        errors_when_locked = []
        lock = importer._lock

        def record_errors_then_lock():
            errors_when_locked.append(dict(importer.errors))
            lock()

        importer._lock = record_errors_then_lock
        importer.run()

        assert errors_when_locked == [{"Area slug is required and it should be unique.": [2]}]

    def test_import_with_missing_columns_does_not_lock_the_opportunity(self, opportunity):
        with CaptureQueriesContext(connection) as queries:
            result = WorkAreaCSVImporter(opportunity.id, self.build_csv([["area-1"]], headers=["Area Slug"])).run()

        assert any(message.startswith("Missing columns") for message in result["errors"])
        assert not any("FOR NO KEY UPDATE" in query["sql"] for query in queries)

    def test_import_reports_existing_slugs_with_other_row_errors(self, opportunity):
        WorkAreaFactory(opportunity=opportunity, slug="area-2")
        rows = [self.build_row("area-1"), self.build_row("area-2"), self.build_row("")]

        result = WorkAreaCSVImporter(opportunity.id, self.build_csv(rows)).run()

        assert result == {
            "errors": {
                "Area slug is required and it should be unique.": [4],
                "Area slug already exists for this opportunity": [3],
            }
        }
        assert WorkArea.objects.filter(opportunity=opportunity).count() == 1

    def test_import_with_no_rows(self, opportunity):
        result = WorkAreaCSVImporter(opportunity.id, self.build_csv([])).run()

        assert result == {"errors": {"The file has no data rows. Add at least one row below the header.": [1]}}
        assert WorkArea.objects.filter(opportunity=opportunity).count() == 0

    def test_import_accepts_groups_each_in_one_ward(self, opportunity):
        rows = [
            self.build_row("area-1", "group-a", ward="Dandi"),
            self.build_row("area-2", "group-b", ward="Dabaza"),
            self.build_row("area-3", "group-a", ward="Dandi"),
        ]
        result = WorkAreaCSVImporter(opportunity.id, self.build_csv_with_groups(rows)).run()

        assert result == {"created": 3}
        wards = dict(WorkAreaGroup.objects.filter(opportunity=opportunity).values_list("name", "ward"))
        assert wards == {"group-a": "Dandi", "group-b": "Dabaza"}

    def test_import_rejects_other_spellings_of_a_groups_ward(self, opportunity):
        WorkAreaGroupFactory(opportunity=opportunity, name="group-a", ward="tudu-kwaya")
        rows = [
            self.build_row("area-1", "group-a", ward="Tudu Kwaya"),
            self.build_row("area-2", "group-b", ward="Ward South"),
            self.build_row("area-3", "group-b", ward="ward south"),
        ]
        result = WorkAreaCSVImporter(opportunity.id, self.build_csv_with_groups(rows)).run()

        assert result == {
            "errors": {
                "Work Area Group group-a is in ward tudu-kwaya, but these rows give ward Tudu Kwaya. "
                "Correct the ward, or use a different group name.": [2],
                "Work Area Group group-b is in ward Ward South, but these rows give ward ward south. "
                "Correct the ward, or use a different group name.": [4],
            }
        }

    def test_import_reports_only_rows_in_another_ward_than_a_new_groups_first_row(self, opportunity):
        rows = [
            self.build_row("area-1", "group-a", ward="Dandi"),
            self.build_row("area-2", "group-a", ward="Dandi"),
            self.build_row("area-3", "group-a", ward="Todi"),
        ]
        result = WorkAreaCSVImporter(opportunity.id, self.build_csv_with_groups(rows)).run()

        assert result == {
            "errors": {
                "Work Area Group group-a is in ward Dandi, but these rows give ward Todi. "
                "Correct the ward, or use a different group name.": [4]
            }
        }

    def test_row_without_ward_is_not_reported_as_a_ward_mismatch(self, opportunity):
        rows = [
            self.build_row("area-1", "group-a", ward="Dandi"),
            self.build_row("area-2", "group-a", ward=""),
        ]
        result = WorkAreaCSVImporter(opportunity.id, self.build_csv_with_groups(rows)).run()

        assert result == {"errors": {"Ward is required.": [3]}}

    def test_ward_mismatch_is_reported_alongside_missing_group_names(self, opportunity):
        rows = [
            self.build_row("area-1", "group-a", ward="Dandi"),
            self.build_row("area-2", "group-a", ward="Dabaza"),
            self.build_row("area-3", "", ward="Dandi"),
        ]
        result = WorkAreaCSVImporter(opportunity.id, self.build_csv_with_groups(rows)).run()

        assert set(result["errors"]) == {
            "Work Area Group Name is required for all Work Areas, or must be omitted for all.",
            "Work Area Group group-a is in ward Dandi, but these rows give ward Dabaza. "
            "Correct the ward, or use a different group name.",
        }

    # --- adding to an opportunity that already has work areas ---

    @pytest.fixture
    def sync_delay(self):
        with mock.patch("commcare_connect.microplanning.tasks.sync_work_area_group_cases.delay") as delay:
            yield delay

    def existing_group(self, opportunity, *member_squares, **work_area_fields):
        """Group "group-a" in ward "ward", holding a work area on each square, with its centre point set."""
        group = WorkAreaGroupFactory(opportunity=opportunity, name="group-a", ward="ward")
        for x, y in member_squares:
            WorkAreaFactory(opportunity=opportunity, work_area_group=group, boundary=square(x, y), **work_area_fields)
        group.update_centroid()
        return WorkAreaGroup.objects.get(pk=group.pk)

    def add_to_group_a(self, opportunity, x=12):
        """Upload work area "area-new" into group-a, on the square at (x, 10)."""
        rows = [self.build_row(slug="area-new", group_name="group-a", boundary=square(x, 10))]
        return WorkAreaCSVImporter(opp_id=opportunity.id, csv_source=self.build_csv_with_groups(rows)).run()

    def test_adding_leaves_existing_work_areas_unchanged(self, opportunity):
        access = OpportunityAccessFactory(opportunity=opportunity)
        group = self.existing_group(opportunity, (10, 10), opportunity_access=access, status=WorkAreaStatus.VISITED)
        existing = group.workarea_set.get()
        UserVisitFactory(opportunity=opportunity, user=access.user, opportunity_access=access, work_area=existing)
        saved_before = WorkArea.objects.values().get(pk=existing.pk)

        result = self.add_to_group_a(opportunity)

        assert result == {"created": 1}
        assert WorkArea.objects.values().get(pk=existing.pk) == saved_before
        assert existing.uservisit_set.count() == 1
        new = WorkArea.objects.get(opportunity=opportunity, slug="area-new")
        assert (new.status, new.opportunity_access, new.work_area_group) == (WorkAreaStatus.UNASSIGNED, None, group)

    def test_adding_to_existing_group_moves_its_centre_point(self, opportunity):
        group = self.existing_group(opportunity, (10, 10))

        self.add_to_group_a(opportunity)

        group.refresh_from_db()
        assert group.centroid.coords == (11.5, 10.5)

    @pytest.mark.parametrize(
        "status",
        [
            pytest.param(WorkAreaStatus.UNASSIGNED, id="had-a-centre-point"),
            # An inaccessible work area doesn't count towards the centre point, so the group had none.
            pytest.param(WorkAreaStatus.INACCESSIBLE, id="had-no-centre-point"),
        ],
    )
    def test_sync_queued_for_existing_groups_that_moved(
        self, opportunity, django_capture_on_commit_callbacks, sync_delay, status
    ):
        group = self.existing_group(opportunity, (10, 10), status=status)
        rows = [
            self.build_row(slug="area-new", group_name="group-a", boundary=square(12, 10)),
            self.build_row(slug="area-other", group_name="new-group", boundary=square(14, 10)),
        ]

        with django_capture_on_commit_callbacks(execute=True):
            result = WorkAreaCSVImporter(opp_id=opportunity.id, csv_source=self.build_csv_with_groups(rows)).run()

        assert result == {"created": 2}
        sync_delay.assert_called_once_with(opportunity_id=opportunity.id, work_area_group_ids=[group.pk])

    @pytest.mark.parametrize(
        "group_name",
        [
            pytest.param(None, id="no-group-column"),
            pytest.param("new-group", id="only-new-groups"),
            # The new square is between group-a's two members, so the group's centre point stays where it was.
            pytest.param("group-a", id="centre-point-unchanged"),
        ],
    )
    def test_sync_not_queued(self, opportunity, django_capture_on_commit_callbacks, sync_delay, group_name):
        self.existing_group(opportunity, (10, 10), (14, 10))
        row = self.build_row(slug="area-new", group_name=group_name, boundary=square(12, 10))
        csv_file = self.build_csv_with_groups([row]) if group_name else self.build_csv([row])

        with django_capture_on_commit_callbacks(execute=True):
            result = WorkAreaCSVImporter(opp_id=opportunity.id, csv_source=csv_file).run()

        assert result == {"created": 1}
        sync_delay.assert_not_called()

    def test_sync_not_queued_when_the_upload_is_rejected(
        self, opportunity, django_capture_on_commit_callbacks, sync_delay
    ):
        self.existing_group(opportunity, (10, 10))

        with django_capture_on_commit_callbacks(execute=True):
            result = self.add_to_group_a(opportunity=opportunity, x=10)  # on top of the existing work area

        assert "errors" in result
        sync_delay.assert_not_called()

    def test_upload_succeeds_when_the_sync_cannot_be_queued(
        self, opportunity, django_capture_on_commit_callbacks, sync_delay
    ):
        self.existing_group(opportunity, (10, 10))
        sync_delay.side_effect = OperationalError("broker unreachable")

        with django_capture_on_commit_callbacks(execute=True):
            result = self.add_to_group_a(opportunity=opportunity)

        assert result == {"created": 1}
        assert WorkArea.objects.filter(opportunity=opportunity, slug="area-new").exists()

    def test_sync_queued_only_once_the_upload_commits(
        self, opportunity, django_capture_on_commit_callbacks, sync_delay
    ):
        group = self.existing_group(opportunity, (10, 10))

        with django_capture_on_commit_callbacks(execute=False) as callbacks:
            self.add_to_group_a(opportunity)
        sync_delay.assert_not_called()

        assert len(callbacks) == 1
        callbacks[0]()
        sync_delay.assert_called_once_with(opportunity_id=opportunity.id, work_area_group_ids=[group.pk])


@pytest.mark.parametrize(
    "ratio, expected",
    [
        (0.0504, "5.1"),
        (0.050000002, "5.1"),  # the smallest ratio the check rejects still reads as over the limit
        (0.05000000000023, "5.0"),  # floating-point noise in an exact 5%
        (0.06000000000002, "6.0"),
        (0.25, "25.0"),
        (1.0, "100.0"),
    ],
)
def test_overlap_percent_never_rounds_down_to_the_limit(ratio, expected):
    assert _overlap_percent(ratio) == expected


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
def test_import_work_areas_task_adds_to_existing_work_areas(opportunity):
    WorkAreaFactory(opportunity=opportunity, boundary=square(10, 10))
    file_name = "test-add-upload.csv"
    default_storage.save(file_name, ContentFile(f"{WA_BASE_HEADER}\n{WA_ROW.format(n=1)}\n".encode()))

    result = import_work_areas_task.apply(args=[opportunity.id, file_name]).get()

    assert result == {"created": 1}
    assert WorkArea.objects.filter(opportunity=opportunity).count() == 2


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
class TestSyncWorkAreaGroupCases:
    GROUP_CENTROID = Point(77.5, 28.5, srid=SRID)

    @pytest.fixture
    def bulk_update(self):
        with mock.patch("commcare_connect.microplanning.tasks.bulk_create_or_update_cases") as bulk_update:
            yield bulk_update

    @pytest.fixture
    def group(self, opportunity):
        return WorkAreaGroupFactory(opportunity=opportunity, centroid=self.GROUP_CENTROID)

    def assigned_work_area(self, group, status=WorkAreaStatus.NOT_VISITED, **kwargs):
        return WorkAreaFactory(
            opportunity=group.opportunity,
            work_area_group=group,
            opportunity_access=OpportunityAccessFactory(opportunity=group.opportunity),
            status=status,
            **kwargs,
        )

    def sync_kwargs(self, opportunity, groups):
        return {"opportunity_id": opportunity.id, "work_area_group_ids": [group.pk for group in groups]}

    def hq_error(self, status_code=None):
        """The error bulk_create_or_update_cases raises when HQ answers with status_code, or doesn't answer."""
        request = httpx.Request("POST", "https://hq.example/a/domain/api/case/v2/")
        if status_code is None:
            cause = httpx.ConnectError("connection refused", request=request)
        else:
            response = httpx.Response(status_code, request=request)
            cause = httpx.HTTPStatusError("HQ error", request=request, response=response)
        error = CommCareHQAPIException("Failed to bulk update cases")
        error.__cause__ = cause
        return error

    def apply_sync(self, opportunity, group, caplog, level):
        """Run the task as Celery would, retries included, capturing its log records at level and above."""
        with caplog.at_level(level, logger="commcare_connect.microplanning.tasks"):
            return sync_work_area_group_cases.apply(kwargs=self.sync_kwargs(opportunity=opportunity, groups=[group]))

    def sent_properties(self, bulk_update):
        """case_id -> properties of every case the task sent, checking it sent them in one call."""
        bulk_update.assert_called_once()
        return {case["case_id"]: case["properties"] for case in bulk_update.call_args.kwargs["cases_data"]}

    def test_sends_only_the_centre_point_to_assigned_work_areas_in_given_groups(self, opportunity, group, bulk_update):
        assigned = self.assigned_work_area(group)
        WorkAreaFactory(opportunity=opportunity, work_area_group=group)
        self.assigned_work_area(WorkAreaGroupFactory(opportunity=opportunity, centroid=self.GROUP_CENTROID))

        sync_work_area_group_cases(**self.sync_kwargs(opportunity=opportunity, groups=[group]))

        bulk_update.assert_called_once_with(
            api_key=opportunity.api_key,
            domain=opportunity.deliver_app.cc_domain,
            cases_data=[{"case_id": assigned.case_id, "properties": {"wag_centroid": "28.50000 77.50000"}}],
        )

    @pytest.mark.parametrize(
        "status",
        [
            WorkAreaStatus.NOT_VISITED,
            WorkAreaStatus.VISITED,
            WorkAreaStatus.EXPECTED_VISIT_REACHED,
            WorkAreaStatus.REQUEST_FOR_INACCESSIBLE,
            WorkAreaStatus.INACCESSIBLE,
        ],
    )
    def test_sends_every_assigned_status(self, opportunity, group, bulk_update, status):
        assigned = self.assigned_work_area(group, status=status)

        sync_work_area_group_cases(**self.sync_kwargs(opportunity=opportunity, groups=[group]))

        assert list(self.sent_properties(bulk_update)) == [assigned.case_id]

    def test_sends_the_centre_point_the_group_has_when_the_task_runs(self, opportunity, group, bulk_update):
        assigned = self.assigned_work_area(group)
        WorkAreaGroup.objects.filter(pk=group.pk).update(centroid=Point(36.8, -1.3, srid=SRID))

        sync_work_area_group_cases(**self.sync_kwargs(opportunity=opportunity, groups=[group]))

        assert self.sent_properties(bulk_update) == {assigned.case_id: {"wag_centroid": "-1.30000 36.80000"}}

    def test_skips_work_areas_without_a_case_and_groups_of_other_opportunities(self, opportunity, group, bulk_update):
        self.assigned_work_area(group, case_id=None)
        other_group = WorkAreaGroupFactory(opportunity=OpportunityFactory(), centroid=self.GROUP_CENTROID)
        self.assigned_work_area(other_group)

        sync_work_area_group_cases(**self.sync_kwargs(opportunity=opportunity, groups=[group, other_group]))

        bulk_update.assert_not_called()

    @pytest.mark.parametrize("missing", ["api_key", "deliver_app", "hq_server"])
    def test_stops_without_hq_setup(self, opportunity, group, bulk_update, caplog, missing):
        self.assigned_work_area(group)
        owner = opportunity.api_key if missing == "hq_server" else opportunity
        setattr(owner, missing, None)
        owner.save(update_fields=[missing])

        result = self.apply_sync(opportunity=opportunity, group=group, caplog=caplog, level="WARNING")

        assert result.successful()
        bulk_update.assert_not_called()
        assert [record.levelname for record in caplog.records] == ["WARNING"]
        assert str(opportunity.id) in caplog.records[0].getMessage()

    @pytest.mark.parametrize("status_code", [400, 403])
    def test_logs_and_skips_a_request_hq_rejects(self, opportunity, group, bulk_update, caplog, status_code):
        rejected = self.assigned_work_area(group)
        sent = self.assigned_work_area(group)
        bulk_update.side_effect = [self.hq_error(status_code=status_code), []]

        with mock.patch("commcare_connect.microplanning.tasks.HQ_CASE_BULK_CHUNK_SIZE", 1):
            result = self.apply_sync(opportunity=opportunity, group=group, caplog=caplog, level="ERROR")

        assert result.successful()
        sent_case_ids = [call.kwargs["cases_data"][0]["case_id"] for call in bulk_update.call_args_list]
        assert sent_case_ids == [rejected.case_id, sent.case_id]
        assert [record.levelname for record in caplog.records] == ["ERROR"]

    @pytest.mark.parametrize(
        "status_code",
        [
            pytest.param(None, id="no-answer"),
            pytest.param(500, id="server-error"),
            pytest.param(429, id="too-many-requests"),
        ],
    )
    def test_retries_on_hq_error_then_fails(self, opportunity, group, bulk_update, status_code):
        self.assigned_work_area(group)
        bulk_update.side_effect = self.hq_error(status_code=status_code)

        result = sync_work_area_group_cases.apply(kwargs=self.sync_kwargs(opportunity=opportunity, groups=[group]))

        assert result.failed()
        assert bulk_update.call_count == 6  # the first try and 5 retries


@pytest.mark.django_db
def test_work_area_import_matches_implementation_area_by_name(opportunity):
    ia = ImplementationAreaFactory(opportunity=opportunity, name="Ward North")
    content = io.StringIO(f"{WA_BASE_HEADER},Implementation Area\n" + WA_ROW.format(n=1) + ",Ward North\n")
    result = WorkAreaCSVImporter(opportunity.id, content).run()
    assert result == {"created": 1}
    assert WorkArea.objects.get(slug="area-1").implementation_area_id == ia.id


@pytest.mark.django_db
def test_work_area_import_unmatched_implementation_area_is_null_but_name_kept(opportunity):
    content = io.StringIO(f"{WA_BASE_HEADER},Implementation Area\n" + WA_ROW.format(n=1) + ",Nonexistent\n")
    result = WorkAreaCSVImporter(opportunity.id, content).run()
    assert result == {"created": 1}
    work_area = WorkArea.objects.get(slug="area-1")
    assert work_area.implementation_area_id is None
    # Name is kept so a later Implementation Area upload can complete the link.
    assert work_area.implementation_area_name == "Nonexistent"


@pytest.mark.django_db
def test_work_area_import_without_implementation_area_column(opportunity):
    content = io.StringIO(f"{WA_BASE_HEADER}\n" + WA_ROW.format(n=1) + "\n")
    result = WorkAreaCSVImporter(opportunity.id, content).run()
    assert result == {"created": 1}
    work_area = WorkArea.objects.get(slug="area-1")
    assert work_area.implementation_area_id is None
    assert work_area.implementation_area_name == ""


@pytest.mark.django_db
def test_implementation_area_import_backlinks_existing_work_areas(opportunity):
    # Work Areas uploaded first, before any Implementation Area exists: FK stays null, name kept.
    wa_content = io.StringIO(f"{WA_BASE_HEADER},Implementation Area\n" + WA_ROW.format(n=1) + ",Ward North\n")
    assert WorkAreaCSVImporter(opportunity.id, wa_content).run() == {"created": 1}
    assert WorkArea.objects.get(slug="area-1").implementation_area_id is None

    # Uploading the matching Implementation Area afterwards links the existing work area.
    ia_content = io.StringIO(f'{IA_HEADER}\nWard North,77.1 28.6,"{IA_POLY}"\n')
    assert ImplementationAreaCSVImporter(opportunity.id, ia_content).run() == {"created": 1}

    area = ImplementationArea.objects.get(opportunity=opportunity, name="Ward North")
    assert WorkArea.objects.get(slug="area-1").implementation_area_id == area.id


@pytest.mark.django_db
def test_implementation_area_import_does_not_link_unrelated_work_areas(opportunity):
    wa_content = io.StringIO(f"{WA_BASE_HEADER},Implementation Area\n" + WA_ROW.format(n=1) + ",Ward North\n")
    WorkAreaCSVImporter(opportunity.id, wa_content).run()

    ia_content = io.StringIO(f'{IA_HEADER}\nWard South,77.1 28.6,"{IA_POLY}"\n')
    ImplementationAreaCSVImporter(opportunity.id, ia_content).run()

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
        (f',77.1 28.6,"{IA_POLY}"', "Implementation Area Name is required and should be unique."),
        (f'Ward,bad-centroid,"{IA_POLY}"', "Centroid must be in 'lon lat' format"),
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
    content = io.StringIO(f'{IA_HEADER}\nWard,77.1 28.6,"{IA_POLY}"\nWard,77.2 28.7,"{IA_POLY}"\n')
    result = ImplementationAreaCSVImporter(opportunity.id, content).run()
    assert "Duplicate Implementation Area Name in file" in result["errors"]


@pytest.mark.django_db
def test_implementation_area_name_that_already_exists(opportunity):
    ImplementationAreaFactory(opportunity=opportunity, name="Ward")
    content = io.StringIO(f'{IA_HEADER}\nWard,77.1 28.6,"{IA_POLY}"\nOther,77.2 28.7,"{IA_POLY}"\n')
    result = ImplementationAreaCSVImporter(opportunity.id, content).run()
    assert result == {"errors": {"Implementation Area Name already exists for this opportunity": [2]}}
    assert ImplementationArea.objects.filter(opportunity=opportunity).count() == 1


@pytest.mark.django_db
def test_implementation_area_short_row_reports_boundary_error(opportunity):
    # A row with fewer fields than headers leaves boundary as None; it must surface as a
    # clean per-row validation error, not crash the import with an AttributeError.
    content = io.StringIO(f"{IA_HEADER}\nWard,77.1 28.6\n")
    result = ImplementationAreaCSVImporter(opportunity.id, content).run()
    assert "Invalid WKT format for Boundary(Polygon)." in result["errors"]


@pytest.mark.django_db
@pytest.mark.parametrize(
    "importer_class,header",
    [(ImplementationAreaCSVImporter, IA_HEADER), (WorkAreaCSVImporter, WA_BASE_HEADER)],
)
@pytest.mark.parametrize("body", ["", "{header}\n", "{header}\n\n\n"])
def test_import_without_data_rows_reports_error(opportunity, importer_class, header, body):
    content = io.StringIO(body.format(header=header))
    result = importer_class(opportunity.id, content).run()
    if body:
        expected_error = "The file has no data rows. Add at least one row below the header."
    else:
        expected_error = "Missing columns: " + ", ".join(sorted(importer_class.HEADERS.values()))
    assert not importer_class.model.objects.filter(opportunity_id=opportunity.id).exists()
    assert result == {"errors": {expected_error: [1]}}


@pytest.mark.django_db
def test_implementation_area_missing_columns(opportunity):
    content = io.StringIO("Implementation Area Name,Centroid\nWard,77.1 28.6\n")
    result = ImplementationAreaCSVImporter(opportunity.id, content).run()
    assert any("Missing columns" in msg for msg in result["errors"])


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


@pytest.mark.django_db
def test_update_overture_release_records_the_latest_release(httpx_mock):
    httpx_mock.add_response(url=OVERTURE_CATALOG_URL, json={"latest": "2026-09-17.0"})

    update_overture_release()

    assert OvertureRelease.current() == "2026-09-17.0"


@pytest.mark.parametrize(
    "catalog_answer",
    [
        {"json": {}},
        {"json": {"latest": None}},
        {"json": {"latest": ""}},
        {"json": {"latest": "latest"}},
        {"json": {"latest": "2026-09-17"}},  # a date, but no revision
        {"json": {"latest": "../../etc/passwd"}},
        {"json": {"latest": ["2026-09-17.0"]}},
        {"text": "<html>502 Bad Gateway</html>"},  # answered, but not with JSON
        {"text": "null"},  # JSON, but nothing to read "latest" off
        {"json": []},
        {"json": "2026-09-17.0"},
    ],
)
def test_a_release_we_cannot_use_is_not_recorded(httpx_mock, overture_release, catalog_answer):
    httpx_mock.add_response(url=OVERTURE_CATALOG_URL, **catalog_answer)

    update_overture_release()

    assert OvertureRelease.current() == overture_release


def test_a_catalog_we_cannot_reach_leaves_the_previous_release_standing(httpx_mock, overture_release):
    httpx_mock.add_response(url=OVERTURE_CATALOG_URL, status_code=503)

    with pytest.raises(httpx.HTTPStatusError):
        update_overture_release()

    assert OvertureRelease.current() == overture_release


@pytest.mark.django_db
def test_the_release_is_rewritten_in_place_rather_than_accumulating_rows(httpx_mock):
    for release in ("2026-09-17.0", "2026-10-22.0"):
        httpx_mock.add_response(url=OVERTURE_CATALOG_URL, json={"latest": release})
        update_overture_release()

    assert OvertureRelease.objects.count() == 1
    assert OvertureRelease.current() == "2026-10-22.0"
