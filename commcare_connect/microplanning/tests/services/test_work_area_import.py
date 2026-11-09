import csv
import io
import itertools

import pytest
from django.contrib.gis.geos import Polygon
from django.db import connection
from django.test.utils import CaptureQueriesContext

from commcare_connect.microplanning.const import WORK_AREA_CSV_GROUP_NAME_HEADER
from commcare_connect.microplanning.models import SRID, WorkArea, WorkAreaGroup
from commcare_connect.microplanning.services.work_area_import import (
    _overlap_percent,
    import_uploaded_work_areas,
    import_work_areas,
)
from commcare_connect.microplanning.tests.factories import (
    ImplementationAreaFactory,
    WorkAreaFactory,
    WorkAreaGroupFactory,
    square,
)

WA_BASE_HEADER = "Area Slug,Ward,Centroid,Boundary,Building Count,Expected Visit Count,Target Population,LGA,State"
WA_ROW = 'area-{n},Ward1,77.1 28.6,"POLYGON((77 28,78 28,78 29,77 29,77 28))",5,6,7,LGA1,State1'


@pytest.fixture
def work_area(opportunity):
    return WorkAreaFactory(opportunity=opportunity)


@pytest.mark.django_db
class TestImportWorkAreas:
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
        result = import_work_areas(opportunity_id=opportunity.id, csv_source=csv)
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

        result = import_work_areas(opportunity_id=opportunity.id, csv_source=csv)
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
        result = import_work_areas(opportunity_id=opportunity.id, csv_source=self.build_csv(rows))
        assert "errors" in result
        assert "duplicate" in " ".join(result["errors"].keys()).lower()

    def test_slug_exists_in_db(self, opportunity, work_area):
        csv = self.build_csv([[work_area.slug, self.CENTROID, self.POLYGON, "1", "1"]])
        result = import_work_areas(opportunity_id=opportunity.id, csv_source=csv)
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
        result = import_work_areas(opportunity_id=opportunity.id, csv_source=csv_data)

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
        result = import_work_areas(opportunity_id=opportunity.id, csv_source=csv_data)

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
        result = import_work_areas(opportunity_id=opportunity.id, csv_source=csv)
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
        headers = self.HEADERS + [WORK_AREA_CSV_GROUP_NAME_HEADER]
        return self.build_csv(rows, headers=headers)

    def test_import_assigns_work_area_groups_when_all_rows_have_one(self, opportunity):
        rows = [
            self.build_row("area-1", "group-a"),
            self.build_row("area-2", "group-a"),
            self.build_row("area-3", "group-b"),
        ]
        result = import_work_areas(opportunity_id=opportunity.id, csv_source=self.build_csv_with_groups(rows))

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
        result = import_work_areas(opportunity_id=opportunity.id, csv_source=self.build_csv(rows))

        assert result["created"] == 2
        assert not WorkAreaGroup.objects.filter(opportunity=opportunity).exists()
        assert WorkArea.objects.get(slug="area-1").work_area_group is None
        assert WorkArea.objects.get(slug="area-2").work_area_group is None

    def test_import_errors_when_only_some_rows_have_a_group(self, opportunity):
        rows = [
            self.build_row("area-1", "group-a"),
            self.build_row("area-2", ""),
        ]
        result = import_work_areas(opportunity_id=opportunity.id, csv_source=self.build_csv_with_groups(rows))

        assert "errors" in result
        error_keys = " ".join(result["errors"].keys()).lower()
        assert "work area group name" in error_keys
        assert WorkArea.objects.count() == 0
        assert not WorkAreaGroup.objects.filter(opportunity=opportunity).exists()

    def test_import_reuses_existing_work_area_group_with_same_name(self, opportunity):
        existing_group = WorkAreaGroupFactory(opportunity=opportunity, name="group-a", ward="ward")
        rows = [self.build_row("area-1", "group-a")]
        result = import_work_areas(opportunity_id=opportunity.id, csv_source=self.build_csv_with_groups(rows))

        assert result["created"] == 1
        assert WorkAreaGroup.objects.filter(opportunity=opportunity, name="group-a").count() == 1
        assert WorkArea.objects.get(slug="area-1").work_area_group == existing_group

    def test_import_next_to_existing_work_area(self, opportunity):
        WorkAreaFactory(opportunity=opportunity, slug="existing", boundary=square(0, 0))
        rows = [self.build_row("area-1", boundary=square(1, 0))]  # shares an edge

        result = import_work_areas(opportunity_id=opportunity.id, csv_source=self.build_csv(rows))

        assert result == {"created": 1}

    def test_overlap_with_existing_work_area_is_rejected(self, opportunity):
        existing = WorkAreaFactory(opportunity=opportunity, slug="existing", boundary=square(0, 0))
        rows = [
            self.build_row("area-1"),
            self.build_row("area-2", boundary=square(0.75, 0)),
        ]

        result = import_work_areas(opportunity_id=opportunity.id, csv_source=self.build_csv(rows))

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

        result = import_work_areas(opportunity_id=opportunity.id, csv_source=self.build_csv(rows))

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
        result = import_work_areas(opportunity_id=opportunity.id, csv_source=self.build_csv_with_groups(rows))

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
        result = import_work_areas(opportunity_id=opportunity.id, csv_source=self.build_csv_with_groups(rows))

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

        result = import_work_areas(opportunity_id=opportunity.id, csv_source=self.build_csv(rows))

        assert len(result["errors"]) == 51
        assert list(result["errors"].items())[-1] == ("2 more overlaps not shown.", [1])

    def test_rejected_import_leaves_no_new_groups(self, opportunity):
        WorkAreaFactory(opportunity=opportunity, slug="existing", boundary=square(0, 0))
        rows = [self.build_row("area-1", "new-group", boundary=square(0, 0))]

        result = import_work_areas(opportunity_id=opportunity.id, csv_source=self.build_csv_with_groups(rows))

        assert "errors" in result
        assert not WorkAreaGroup.objects.filter(opportunity=opportunity).exists()
        assert WorkArea.objects.filter(opportunity=opportunity).count() == 1

    def test_self_crossing_boundary_is_a_row_error(self, opportunity):
        bow_tie = Polygon(((0, 0), (1, 1), (1, 0), (0, 1), (0, 0)), srid=SRID)
        rows = [self.build_row("area-1"), self.build_row("area-2", boundary=bow_tie)]

        result = import_work_areas(opportunity_id=opportunity.id, csv_source=self.build_csv(rows))

        assert result == {
            "errors": {
                "Boundary is not a valid polygon, e.g. its edges cross. Fix the boundary, then upload again.": [3]
            }
        }
        assert not WorkArea.objects.filter(opportunity=opportunity).exists()

    def test_import_locks_the_opportunity_before_reading_work_areas_or_groups(self, opportunity):
        csv = self.build_csv_with_groups([self.build_row("area-1", "group-a")])
        with CaptureQueriesContext(connection) as queries:
            import_work_areas(opportunity_id=opportunity.id, csv_source=csv)

        sqls = [query["sql"] for query in queries]
        lock = next(i for i, sql in enumerate(sqls) if "FOR NO KEY UPDATE" in sql)
        first_work_area_query = next(i for i, sql in enumerate(sqls) if '"microplanning_workarea"' in sql)
        first_group_query = next(i for i, sql in enumerate(sqls) if '"microplanning_workareagroup"' in sql)
        assert '"opportunity_opportunity"' in sqls[lock]
        assert lock < first_work_area_query
        assert lock < first_group_query

    def test_import_refreshes_statistics_between_insert_and_overlap_check(self, opportunity):
        with CaptureQueriesContext(connection) as queries:
            import_work_areas(opportunity_id=opportunity.id, csv_source=self.build_csv([self.build_row("area-1")]))

        sqls = [query["sql"] for query in queries]
        insert = next(i for i, sql in enumerate(sqls) if sql.startswith('INSERT INTO "microplanning_workarea"'))
        analyze = next(i for i, sql in enumerate(sqls) if sql.startswith("ANALYZE"))
        overlap_check = next(i for i, sql in enumerate(sqls) if "ST_Intersection" in sql)
        assert sqls[analyze] == 'ANALYZE "microplanning_workarea" ("opportunity_id")'
        assert insert < analyze < overlap_check

    def test_import_with_missing_columns_does_not_lock_the_opportunity(self, opportunity):
        with CaptureQueriesContext(connection) as queries:
            result = import_work_areas(
                opportunity_id=opportunity.id, csv_source=self.build_csv([["area-1"]], headers=["Area Slug"])
            )

        assert any(message.startswith("Missing columns") for message in result["errors"])
        assert not any("FOR NO KEY UPDATE" in query["sql"] for query in queries)

    def test_import_reports_existing_slugs_with_other_row_errors(self, opportunity):
        WorkAreaFactory(opportunity=opportunity, slug="area-2")
        rows = [self.build_row("area-1"), self.build_row("area-2"), self.build_row("")]

        result = import_work_areas(opportunity_id=opportunity.id, csv_source=self.build_csv(rows))

        assert result == {
            "errors": {
                "Area slug is required and it should be unique.": [4],
                "Area slug already exists for this opportunity": [3],
            }
        }
        assert WorkArea.objects.filter(opportunity=opportunity).count() == 1

    def test_import_with_no_rows(self, opportunity):
        result = import_work_areas(opportunity_id=opportunity.id, csv_source=self.build_csv([]))

        assert result == {"created": 0}

    def test_import_accepts_groups_each_in_one_ward(self, opportunity):
        rows = [
            self.build_row("area-1", "group-a", ward="Dandi"),
            self.build_row("area-2", "group-b", ward="Dabaza"),
            self.build_row("area-3", "group-a", ward="Dandi"),
        ]
        result = import_work_areas(opportunity_id=opportunity.id, csv_source=self.build_csv_with_groups(rows))

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
        result = import_work_areas(opportunity_id=opportunity.id, csv_source=self.build_csv_with_groups(rows))

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
        result = import_work_areas(opportunity_id=opportunity.id, csv_source=self.build_csv_with_groups(rows))

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
        result = import_work_areas(opportunity_id=opportunity.id, csv_source=self.build_csv_with_groups(rows))

        assert result == {"errors": {"Ward is required.": [3]}}

    def test_ward_mismatch_is_reported_alongside_missing_group_names(self, opportunity):
        rows = [
            self.build_row("area-1", "group-a", ward="Dandi"),
            self.build_row("area-2", "group-a", ward="Dabaza"),
            self.build_row("area-3", "", ward="Dandi"),
        ]
        result = import_work_areas(opportunity_id=opportunity.id, csv_source=self.build_csv_with_groups(rows))

        assert set(result["errors"]) == {
            "Work Area Group Name is required for all Work Areas, or must be omitted for all.",
            "Work Area Group group-a is in ward Dandi, but these rows give ward Dabaza. "
            "Correct the ward, or use a different group name.",
        }


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
def test_work_area_import_matches_implementation_area_by_name(opportunity):
    ia = ImplementationAreaFactory(opportunity=opportunity, name="Ward North")
    content = io.StringIO(f"{WA_BASE_HEADER},Implementation Area\n" + WA_ROW.format(n=1) + ",Ward North\n")
    result = import_work_areas(opportunity_id=opportunity.id, csv_source=content)
    assert result == {"created": 1}
    assert WorkArea.objects.get(slug="area-1").implementation_area_id == ia.id


@pytest.mark.django_db
def test_work_area_import_unmatched_implementation_area_is_null_but_name_kept(opportunity):
    content = io.StringIO(f"{WA_BASE_HEADER},Implementation Area\n" + WA_ROW.format(n=1) + ",Nonexistent\n")
    result = import_work_areas(opportunity_id=opportunity.id, csv_source=content)
    assert result == {"created": 1}
    work_area = WorkArea.objects.get(slug="area-1")
    assert work_area.implementation_area_id is None
    # Name is kept so a later Implementation Area upload can complete the link.
    assert work_area.implementation_area_name == "Nonexistent"


@pytest.mark.django_db
def test_work_area_import_without_implementation_area_column(opportunity):
    content = io.StringIO(f"{WA_BASE_HEADER}\n" + WA_ROW.format(n=1) + "\n")
    result = import_work_areas(opportunity_id=opportunity.id, csv_source=content)
    assert result == {"created": 1}
    work_area = WorkArea.objects.get(slug="area-1")
    assert work_area.implementation_area_id is None
    assert work_area.implementation_area_name == ""


@pytest.mark.django_db
def test_import_uploaded_work_areas_refuses_when_work_areas_exist(opportunity):
    WorkAreaFactory(opportunity=opportunity)
    csv_source = io.StringIO(f"{WA_BASE_HEADER}\n" + WA_ROW.format(n=1) + "\n")

    result = import_uploaded_work_areas(opportunity_id=opportunity.id, csv_source=csv_source)

    assert result == {"errors": {"Work Areas already exist for this opportunity": [0]}}
    assert WorkArea.objects.filter(opportunity=opportunity).count() == 1
