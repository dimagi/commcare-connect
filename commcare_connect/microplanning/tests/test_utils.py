import io

import pytest

from commcare_connect.microplanning.exceptions import AreaCSVColumnsError
from commcare_connect.microplanning.utils import (
    parse_implementation_area_csv,
    parse_lon_lat_centroid,
    parse_work_area_csv,
)

WA_HEADER = "area slug,WARD,Centroid,Boundary,Building Count,Expected Visit Count,Target Population,LGA,State"
POLYGON = "POLYGON((77 28,78 28,78 29,77 29,77 28))"


def test_parse_lon_lat_centroid():
    point = parse_lon_lat_centroid(" 77.1 28.6 ")

    assert (point.x, point.y) == (77.1, 28.6)


def test_parse_work_area_csv_reads_rows_with_any_header_case(django_assert_num_queries):
    csv_source = io.StringIO(f'{WA_HEADER}\narea-1,<b>Ward1</b>,77.1 28.6,"{POLYGON}",5,6,7,LGA1,State1\n')

    with django_assert_num_queries(0):
        parsed = parse_work_area_csv(csv_source)

    assert parsed.errors == {}
    [row] = parsed.rows
    assert (row.line_num, row.slug, row.ward, row.group_name) == (2, "area-1", "Ward1", "")
    assert (row.building_count, row.expected_visit_count, row.target_population) == (5, 6, 7)
    assert (row.lga, row.state) == ("LGA1", "State1")
    assert row.boundary.geom_type == "Polygon"


def test_parse_work_area_csv_returns_row_errors_without_querying(django_assert_num_queries):
    csv_source = io.StringIO(f'{WA_HEADER}\n,Ward1,77.1 28.6,"{POLYGON}",5,6,7,LGA1,State1\n')

    with django_assert_num_queries(0):
        parsed = parse_work_area_csv(csv_source)

    assert parsed.errors == {"Area slug is required and it should be unique.": [2]}
    assert len(parsed.rows) == 1


def test_parse_work_area_csv_requires_its_columns():
    with pytest.raises(AreaCSVColumnsError) as error:
        parse_work_area_csv(io.StringIO("Area Slug,Ward\narea-1,Ward1\n"))

    assert error.value.message.startswith("Missing columns: Boundary, Building Count")


def test_parse_implementation_area_csv_reports_duplicate_names():
    csv_source = io.StringIO(
        f'Implementation Area Name,Centroid,Boundary\nWard,77.1 28.6,"{POLYGON}"\nWard,77.1 28.6,"{POLYGON}"\n'
    )

    parsed = parse_implementation_area_csv(csv_source)

    assert parsed.errors == {"Duplicate Implementation Area Name in file": [3]}
    assert [row.name for row in parsed.rows] == ["Ward", "Ward"]
