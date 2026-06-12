import datetime
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.utils import timezone

from commcare_connect.microplanning.coverage_progress import (
    CoverageDateFilter,
    CoverageProgressReport,
    _static_slot,
    annotate_status_timestamps,
    build_wag_rows,
    build_ward_rows,
    get_status_aggregates,
    get_target_aggregates,
    get_visits_approved_aggregates,
    non_excluded_workareas,
    status_event_model,
    ward_saturation_goal,
)
from commcare_connect.microplanning.filters import CoverageProgressFilterSet
from commcare_connect.microplanning.models import WorkArea, WorkAreaStatus
from commcare_connect.microplanning.tests.factories import WorkAreaFactory, WorkAreaGroupFactory
from commcare_connect.opportunity.models import VisitValidationStatus
from commcare_connect.opportunity.tests.factories import UserVisitFactory

pytestmark = pytest.mark.django_db


def test_date_filter_overall_has_no_window():
    f = CoverageDateFilter.overall()
    assert f.is_overall is True
    assert f.window is None


def test_date_filter_custom_range_window():
    f = CoverageDateFilter(start=datetime.date(2026, 1, 1), end=datetime.date(2026, 1, 31))
    assert f.is_overall is False
    # Inclusive date range -> half-open [start midnight, day-after-end midnight) datetime range.
    assert f.window == (
        timezone.make_aware(datetime.datetime(2026, 1, 1, 0, 0)),
        timezone.make_aware(datetime.datetime(2026, 2, 1, 0, 0)),
    )


def test_last_week_window_spans_exactly_seven_days():
    start_dt, end_dt = CoverageDateFilter.last_week().window
    assert (end_dt - start_dt) == datetime.timedelta(days=7)


def _stamp_transition(work_area, status, when):
    Event = status_event_model()
    event = Event.objects.create(
        pgh_obj_id=work_area.pk,
        pgh_label="update",
        status=status,
        expected_visit_count=work_area.expected_visit_count,
        work_area_group_id=work_area.work_area_group_id,
        opportunity_access_id=work_area.opportunity_access_id,
        excluded_reason=work_area.excluded_reason,
    )
    Event.objects.filter(pk=event.pk).update(pgh_created_at=when)
    return event


def test_annotate_status_timestamps_uses_earliest_transition(opportunity):
    wa = WorkAreaFactory(opportunity=opportunity, status=WorkAreaStatus.VISITED)
    early = timezone.make_aware(datetime.datetime(2026, 1, 10, 9, 0))
    late = timezone.make_aware(datetime.datetime(2026, 2, 20, 9, 0))
    _stamp_transition(wa, WorkAreaStatus.VISITED, late)
    _stamp_transition(wa, WorkAreaStatus.VISITED, early)
    _stamp_transition(wa, WorkAreaStatus.EXPECTED_VISIT_REACHED, late)

    annotated = annotate_status_timestamps(non_excluded_workareas(opportunity)).get(pk=wa.pk)
    assert annotated.visited_at == early
    assert annotated.evc_reached_at == late


def test_status_aggregates_overall_strict_and_exclusive(opportunity):
    WorkAreaFactory(opportunity=opportunity, ward="w1", status=WorkAreaStatus.VISITED, building_count=10)
    WorkAreaFactory(opportunity=opportunity, ward="w1", status=WorkAreaStatus.EXPECTED_VISIT_REACHED, building_count=7)
    WorkAreaFactory(opportunity=opportunity, ward="w1", status=WorkAreaStatus.NOT_VISITED, building_count=3)
    WorkAreaFactory(opportunity=opportunity, ward="w1", status=WorkAreaStatus.EXCLUDED, building_count=99)

    result = get_status_aggregates(opportunity, "ward", window=None)

    assert result["w1"]["WAs_visited"] == 1
    assert result["w1"]["WAs_evc_reached"] == 1
    assert result["w1"]["Buildings_covered_in_WAs_visited"] == 10
    assert result["w1"]["Buildings_covered_in_WAs_evc_reached"] == 7


def test_status_aggregates_window_filters_by_transition_date(opportunity):
    wa = WorkAreaFactory(opportunity=opportunity, ward="w1", status=WorkAreaStatus.VISITED, building_count=10)
    _stamp_transition(wa, WorkAreaStatus.VISITED, timezone.make_aware(datetime.datetime(2026, 3, 15, 9, 0)))
    in_window = (datetime.date(2026, 3, 1), datetime.date(2026, 3, 31))
    out_window = (datetime.date(2026, 4, 1), datetime.date(2026, 4, 30))

    assert get_status_aggregates(opportunity, "ward", window=in_window)["w1"]["WAs_visited"] == 1
    assert get_status_aggregates(opportunity, "ward", window=out_window).get("w1", {}).get("WAs_visited", 0) == 0


def test_status_aggregates_window_filters_by_transition_date_for_wag(opportunity):
    group = WorkAreaGroupFactory(opportunity=opportunity)
    wa = WorkAreaFactory(
        opportunity=opportunity, work_area_group=group, status=WorkAreaStatus.VISITED, building_count=10
    )
    _stamp_transition(wa, WorkAreaStatus.VISITED, timezone.make_aware(datetime.datetime(2026, 3, 15, 9, 0)))
    in_window = (datetime.date(2026, 3, 1), datetime.date(2026, 3, 31))
    out_window = (datetime.date(2026, 4, 1), datetime.date(2026, 4, 30))

    in_result = get_status_aggregates(opportunity, "work_area_group_id", window=in_window)
    out_result = get_status_aggregates(opportunity, "work_area_group_id", window=out_window)
    assert in_result[group.id]["WAs_visited"] == 1
    assert out_result.get(group.id, {}).get("WAs_visited", 0) == 0


def test_target_aggregates_by_ward_excludes_excluded(opportunity):
    WorkAreaFactory(
        opportunity=opportunity,
        ward="w1",
        status=WorkAreaStatus.VISITED,
        target_population=100,
        building_count=10,
        expected_visit_count=5,
    )
    WorkAreaFactory(
        opportunity=opportunity,
        ward="w1",
        status=WorkAreaStatus.INACCESSIBLE,
        target_population=50,
        building_count=4,
        expected_visit_count=3,
    )
    WorkAreaFactory(
        opportunity=opportunity,
        ward="w1",
        status=WorkAreaStatus.EXCLUDED,
        target_population=999,
        building_count=99,
        expected_visit_count=99,
    )
    WorkAreaFactory(
        opportunity=opportunity,
        ward="w2",
        status=WorkAreaStatus.NOT_VISITED,
        target_population=20,
        building_count=2,
        expected_visit_count=1,
    )

    result = get_target_aggregates(opportunity, "ward")

    assert result["w1"] == {
        "ward": "w1",
        "target_population": 150,
        "building_count": 14,
        "num_work_areas": 2,
        "expected_visit_total": 8,
    }
    assert result["w2"]["num_work_areas"] == 1
    assert "999" not in str(result["w1"])  # excluded WA not summed


def test_target_aggregates_by_wag_excludes_excluded(opportunity):
    group = WorkAreaGroupFactory(opportunity=opportunity)
    WorkAreaFactory(
        opportunity=opportunity,
        work_area_group=group,
        status=WorkAreaStatus.VISITED,
        target_population=100,
        building_count=10,
        expected_visit_count=5,
    )
    WorkAreaFactory(
        opportunity=opportunity,
        work_area_group=group,
        status=WorkAreaStatus.EXCLUDED,
        target_population=999,
        building_count=99,
        expected_visit_count=99,
    )

    result = get_target_aggregates(opportunity, "work_area_group_id")

    assert result[group.id] == {
        "work_area_group_id": group.id,
        "target_population": 100,
        "building_count": 10,
        "num_work_areas": 1,
        "expected_visit_total": 5,
    }


def _approved_visit(opportunity, work_area, when):
    return UserVisitFactory(
        opportunity=opportunity,
        work_area=work_area,
        status=VisitValidationStatus.approved,
        visit_date=timezone.make_aware(datetime.datetime.combine(when, datetime.time(9, 0))),
    )


def test_visits_approved_overall_excludes_excluded_and_unapproved(opportunity):
    wa = WorkAreaFactory(opportunity=opportunity, ward="w1", status=WorkAreaStatus.VISITED)
    excluded = WorkAreaFactory(opportunity=opportunity, ward="w1", status=WorkAreaStatus.EXCLUDED)
    _approved_visit(opportunity, wa, datetime.date(2026, 3, 10))
    _approved_visit(opportunity, wa, datetime.date(2026, 3, 12))
    _approved_visit(opportunity, excluded, datetime.date(2026, 3, 12))  # dropped: EXCLUDED WA
    UserVisitFactory(
        opportunity=opportunity,
        work_area=wa,
        status=VisitValidationStatus.pending,
        visit_date=timezone.make_aware(datetime.datetime(2026, 3, 12, 9, 0)),
    )  # dropped: not approved

    result = get_visits_approved_aggregates(opportunity, "ward", window=None)
    assert result["w1"]["visits_approved"] == 2


def test_visits_approved_window_filters_visit_date(opportunity):
    wa = WorkAreaFactory(opportunity=opportunity, ward="w1", status=WorkAreaStatus.VISITED)
    _approved_visit(opportunity, wa, datetime.date(2026, 3, 10))
    _approved_visit(opportunity, wa, datetime.date(2026, 4, 10))
    window = (datetime.date(2026, 3, 1), datetime.date(2026, 3, 31))
    assert get_visits_approved_aggregates(opportunity, "ward", window=window)["w1"]["visits_approved"] == 1


def test_build_ward_rows_merges_and_derives():
    target_aggregates = {
        "w1": {
            "ward": "w1",
            "target_population": 200,
            "building_count": 50,
            "num_work_areas": 10,
            "expected_visit_total": 40,
        }
    }
    filtered_status = {
        "w1": {
            "ward": "w1",
            "WAs_visited": 4,
            "WAs_evc_reached": 2,
            "Buildings_covered_in_WAs_visited": 20,
            "Buildings_covered_in_WAs_evc_reached": 8,
        }
    }
    filtered_visits = {"w1": {"ward": "w1", "visits_approved": 20}}
    last_week_status = {
        "w1": {
            "ward": "w1",
            "WAs_visited": 1,
            "WAs_evc_reached": 0,
            "Buildings_covered_in_WAs_visited": 5,
            "Buildings_covered_in_WAs_evc_reached": 0,
        }
    }
    last_week_visits = {"w1": {"ward": "w1", "visits_approved": 5}}

    rows = build_ward_rows(target_aggregates, filtered_status, filtered_visits, last_week_status, last_week_visits)
    row = next(r for r in rows if r["ward"] == "w1")

    assert row["num_work_areas"] == 10
    assert row["visits_approved"] == 20
    assert row["WAs_visited"] == 4
    assert row["pct_visits_approved"] == 50.0  # 20 / 40
    assert row["pct_WAs_visited"] == 40.0  # 4 / 10
    assert row["pct_WAs_evc_reached"] == 20.0  # 2 / 10
    assert row["pct_Buildings_covered_in_WAs_visited"] == 40.0  # 20 / 50
    assert row["pct_WA_visited_to_pct_visits"] == 80.0  # 40 / 50 * 100
    assert row["pct_WA_evc_reached_to_pct_visit"] == 40.0  # 20 / 50 * 100
    assert row["WAs_visited_last_week"] == 1
    assert row["pct_WAs_visited_last_week"] == 10.0  # 1 / 10
    # last-week ratio = pct_WAs_visited_last_week / pct_visits_approved_last_week * 100
    #                 = 10.0 / (5/40*100 = 12.5) * 100 = 80.0
    assert row["pct_WA_visited_to_pct_visits_last_week"] == 80.0


def test_build_ward_rows_zero_denominator_yields_none():
    target_aggregates = {
        "w1": {
            "ward": "w1",
            "target_population": 0,
            "building_count": 0,
            "num_work_areas": 0,
            "expected_visit_total": 0,
        }
    }
    rows = build_ward_rows(target_aggregates, {}, {}, {}, {})
    row = rows[0]
    assert row["visits_approved"] == 0
    assert row["pct_visits_approved"] is None
    assert row["pct_WAs_visited"] is None
    assert row["pct_WA_visited_to_pct_visits"] is None


def test_build_wag_rows_reduced_columns(opportunity):
    group = WorkAreaGroupFactory(opportunity=opportunity, ward="w1", name="G1")
    target_aggregates = {
        group.id: {
            "work_area_group_id": group.id,
            "target_population": 300,
            "building_count": 60,
            "num_work_areas": 12,
            "expected_visit_total": 50,
        }
    }
    filtered_status = {
        group.id: {
            "work_area_group_id": group.id,
            "WAs_visited": 6,
            "WAs_evc_reached": 3,
            "Buildings_covered_in_WAs_visited": 30,
            "Buildings_covered_in_WAs_evc_reached": 12,
        }
    }
    filtered_visits = {group.id: {"work_area_group_id": group.id, "visits_approved": 25}}
    last_week_status = {
        group.id: {
            "work_area_group_id": group.id,
            "WAs_visited": 2,
            "WAs_evc_reached": 1,
            "Buildings_covered_in_WAs_visited": 10,
            "Buildings_covered_in_WAs_evc_reached": 4,
        }
    }
    last_week_visits = {group.id: {"work_area_group_id": group.id, "visits_approved": 10}}
    display = {group.id: {"work_area_group": "G1", "ward": "w1"}}

    rows = build_wag_rows(
        display, target_aggregates, filtered_status, filtered_visits, last_week_status, last_week_visits
    )
    row = next(r for r in rows if r["work_area_group_id"] == group.id)

    assert row["work_area_group"] == "G1"
    assert row["ward"] == "w1"
    assert row["target_population"] == 300
    assert row["pct_visits_approved"] == 50.0  # 25 / 50
    assert row["pct_WAs_evc_reached"] == 25.0  # 3 / 12
    assert row["pct_WA_visited_to_pct_visits"] == 100.0  # (6/12=50) / (25/50=50) * 100
    # reduced set: building-coverage columns are NOT present
    assert "pct_Buildings_covered_in_WAs_visited" not in row


def test_ward_saturation_goal_rolls_up_opportunity_wide():
    target_aggregates = {"w1": {"num_work_areas": 10}, "w2": {"num_work_areas": 10}}
    status_aggregates = {"w1": {"WAs_evc_reached": 3}, "w2": {"WAs_evc_reached": 2}}
    assert ward_saturation_goal(target_aggregates, status_aggregates) == 25.0  # 5 / 20 * 100


def test_ward_saturation_goal_zero_denominator_is_none():
    assert ward_saturation_goal({}, {}) is None


def test_report_exposes_header_ward_and_wag_rows(opportunity):
    group = WorkAreaGroupFactory(opportunity=opportunity, ward="w1", name="G1")
    wa = WorkAreaFactory(
        opportunity=opportunity,
        ward="w1",
        work_area_group=group,
        status=WorkAreaStatus.EXPECTED_VISIT_REACHED,
        expected_visit_count=2,
        building_count=5,
        target_population=100,
    )
    _approved_visit(opportunity, wa, datetime.date(2026, 5, 30))

    report = CoverageProgressReport(opportunity, CoverageDateFilter.overall())

    assert "ward_saturation_goal" in report.header()
    assert any(r["ward"] == "w1" for r in report.ward_rows())
    assert any(r["work_area_group_id"] == group.id for r in report.wag_rows())


def test_header_saturation_goal_ignores_date_filter(opportunity):
    # Two work areas in one ward; one currently EVC-reached, its transition stamped in March.
    evc = WorkAreaFactory(opportunity=opportunity, ward="w1", status=WorkAreaStatus.EXPECTED_VISIT_REACHED)
    WorkAreaFactory(opportunity=opportunity, ward="w1", status=WorkAreaStatus.NOT_VISITED)
    _stamp_transition(
        evc, WorkAreaStatus.EXPECTED_VISIT_REACHED, timezone.make_aware(datetime.datetime(2026, 3, 15, 9, 0))
    )

    # An April window excludes the March transition, so the *windowed* EVC count would be 0. The
    # header is cumulative, though: 1 of 2 work areas has reached EVC -> 50%, regardless of filter.
    april = CoverageDateFilter(start=datetime.date(2026, 4, 1), end=datetime.date(2026, 4, 30))
    assert CoverageProgressReport(opportunity, april).header()["ward_saturation_goal"] == 50.0


def test_custom_range_bypasses_filtered_cache_slot(opportunity):
    WorkAreaFactory(opportunity=opportunity, ward="w1", status=WorkAreaStatus.VISITED)
    custom = CoverageDateFilter(start=datetime.date(2026, 1, 1), end=datetime.date(2026, 1, 31))
    with patch("commcare_connect.microplanning.coverage_progress._filtered_overall_slot") as overall_slot:
        CoverageProgressReport(opportunity, custom).ward_rows()
        overall_slot.assert_not_called()


def test_last_week_filter_reuses_cached_slot_instead_of_recomputing(opportunity):
    WorkAreaFactory(opportunity=opportunity, ward="w1", status=WorkAreaStatus.VISITED)
    with patch("commcare_connect.microplanning.coverage_progress._compute_filtered") as compute_filtered:
        CoverageProgressReport(opportunity, CoverageDateFilter.last_week()).ward_rows()
        # Last week is served by the cached _last_week_slot, not the uncached _compute_filtered path.
        compute_filtered.assert_not_called()


def test_cache_reuse_keys_on_preset_flag_not_matching_dates(opportunity):
    # A custom range whose dates happen to equal last week's is NOT the preset: routing keys on the
    # explicit flag, so it recomputes rather than depending on a time-sensitive date comparison.
    WorkAreaFactory(opportunity=opportunity, ward="w1", status=WorkAreaStatus.VISITED)
    lw = CoverageDateFilter.last_week()
    same_dates_custom = CoverageDateFilter(start=lw.start, end=lw.end)
    assert same_dates_custom.is_last_week is False
    with patch("commcare_connect.microplanning.coverage_progress._compute_filtered") as compute_filtered:
        compute_filtered.return_value = {"ward_status": {}, "ward_visits": {}, "wag_status": {}, "wag_visits": {}}
        CoverageProgressReport(opportunity, same_dates_custom).ward_rows()
        compute_filtered.assert_called_once()


def test_slot_computes_once_then_serves_cache(opportunity):
    key = f"coverage:static:opp={opportunity.id}"
    cache.delete(key)
    try:
        with patch(
            "commcare_connect.microplanning.coverage_progress.get_target_aggregates",
            return_value={},
        ) as get_target:
            _static_slot(opportunity)  # cold slot -> computes (ward + wag aggregates)
            _static_slot(opportunity)  # warm slot -> served from cache
            assert get_target.call_count == 2  # only the cold call recomputed
    finally:
        cache.delete(key)


def _coverage_filter(data):
    return CoverageProgressFilterSet(data, queryset=WorkArea.objects.none())


def test_filterset_no_params_is_overall():
    assert _coverage_filter({}).to_date_filter().is_overall is True


def test_filterset_last_week_maps_to_last_week_filter():
    assert _coverage_filter({"range": "last_week"}).to_date_filter() == CoverageDateFilter.last_week()


def test_filterset_custom_range_maps_to_custom_window():
    result = _coverage_filter({"range": "custom", "start": "2026-01-01", "end": "2026-01-31"}).to_date_filter()
    assert result == CoverageDateFilter(start=datetime.date(2026, 1, 1), end=datetime.date(2026, 1, 31))


@pytest.mark.parametrize(
    "data",
    [
        {"range": "custom", "start": "2026-01-31", "end": "2026-01-01"},  # reversed
        {"range": "custom", "start": "2026-01-01"},  # incomplete (no end)
        {"range": "custom", "start": "not-a-date", "end": "2026-01-31"},  # invalid date
    ],
)
def test_filterset_invalid_custom_range_falls_back_to_overall(data):
    assert _coverage_filter(data).to_date_filter().is_overall is True


@pytest.mark.parametrize(
    "raw_range, expected",
    [
        (None, "overall"),
        ("last_week", "last_week"),
        ("custom", "custom"),
        ("'+alert(1)+'", "overall"),  # XSS payload is not a valid choice -> collapses to overall
        ("garbage", "overall"),
    ],
)
def test_filterset_selected_range_is_whitelisted(raw_range, expected):
    data = {"range": raw_range} if raw_range is not None else {}
    assert _coverage_filter(data).selected_range == expected


def test_filterset_export_querystring_carries_known_params_plus_export_args():
    qs = _coverage_filter(
        {"range": "custom", "start": "2026-01-01", "end": "2026-01-31", "bogus": "x"}
    ).export_querystring(_export="csv", _table="ward")
    assert qs == "range=custom&start=2026-01-01&end=2026-01-31&_export=csv&_table=ward"


def test_filterset_export_querystring_drops_invalid_range():
    assert _coverage_filter({"range": "garbage"}).export_querystring(_export="csv", _table="ward") == (
        "_export=csv&_table=ward"
    )
