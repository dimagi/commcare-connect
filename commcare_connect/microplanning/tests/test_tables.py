"""Guards that the Coverage Progress tables stay in sync with the report's row-dict contract.

``CoverageWardTable``/``CoverageWAGTable`` reference report keys by string accessor. django-tables2
does not error on a missing accessor and ``NumberColumn`` renders ``None`` as an em-dash, so a
renamed/typo'd key would silently show a blank column. These tests fail loudly instead.
"""

from commcare_connect.microplanning.coverage_progress import build_wag_rows, build_ward_rows
from commcare_connect.microplanning.tables import CoverageWAGTable, CoverageWardTable


def _column_accessors(table):
    return {str(column.accessor) for column in table.columns}


def test_ward_table_columns_match_report_row_keys():
    target = {"w1": {"target_population": 1, "building_count": 1, "num_work_areas": 1, "expected_visit_total": 1}}
    rows = build_ward_rows(target, {}, {}, {}, {})
    table = CoverageWardTable(rows)

    unknown = _column_accessors(table) - set(rows[0].keys())
    assert not unknown, f"CoverageWardTable references keys not emitted by build_ward_rows: {unknown}"


def test_wag_table_columns_match_report_row_keys():
    display = {1: {"work_area_group": "G1", "ward": "w1"}}
    target = {1: {"target_population": 1, "building_count": 1, "num_work_areas": 1, "expected_visit_total": 1}}
    rows = build_wag_rows(display, target, {}, {}, {}, {})
    table = CoverageWAGTable(rows)

    unknown = _column_accessors(table) - set(rows[0].keys())
    assert not unknown, f"CoverageWAGTable references keys not emitted by build_wag_rows: {unknown}"
