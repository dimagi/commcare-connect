import datetime
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.utils import timezone

from commcare_connect.microplanning.const import NO_CHILDREN_WORK_AREA_UNIT_SLUG, SERVICE_DELIVERY_UNIT_SLUG
from commcare_connect.microplanning.coverage_progress import (
    CoverageDateFilter,
    CoverageProgressReport,
    _static_slot,
    annotate_approved_visit_counts,
    build_wag_rows,
    build_ward_rows,
    get_coverage_aggregates,
    get_target_aggregates,
    get_visits_approved_aggregates,
    in_scope_work_areas,
    ward_saturation_goal,
)
from commcare_connect.microplanning.filters import CoverageProgressFilterSet
from commcare_connect.microplanning.models import WorkArea, WorkAreaStatus
from commcare_connect.microplanning.tests.factories import WorkAreaFactory, WorkAreaGroupFactory
from commcare_connect.opportunity.models import VisitValidationStatus
from commcare_connect.opportunity.tests.factories import (
    DeliverUnitFactory,
    OpportunityAccessFactory,
    OpportunityFactory,
    UserVisitFactory,
)

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


def _access(opportunity):
    return OpportunityAccessFactory(opportunity=opportunity)


def test_coverage_aggregates_counts_each_area_once(opportunity):
    """WAs_visited is the union of HSD/NCWA delivery and inaccessible status, not their sum."""
    access = _access(opportunity)
    wa_hsd = WorkAreaFactory(opportunity=opportunity, opportunity_access=access, ward="w1")
    wa_ncwa = WorkAreaFactory(opportunity=opportunity, opportunity_access=access, ward="w1")
    WorkAreaFactory(opportunity=opportunity, opportunity_access=access, ward="w1", status=WorkAreaStatus.INACCESSIBLE)
    WorkAreaFactory(
        opportunity=opportunity, opportunity_access=access, ward="w1", status=WorkAreaStatus.REQUEST_FOR_INACCESSIBLE
    )
    # wa_overlap is both inaccessible and holds an approved HSD visit, so it must count once — same
    # rule as Progress Mapping's Work Areas Done tile.
    wa_overlap = WorkAreaFactory(
        opportunity=opportunity, opportunity_access=access, ward="w1", status=WorkAreaStatus.INACCESSIBLE
    )
    WorkAreaFactory(opportunity=opportunity, opportunity_access=access, ward="w1")  # wa_untouched
    WorkAreaFactory(opportunity=opportunity, ward="w1", status=WorkAreaStatus.EXCLUDED)
    unassigned = WorkAreaFactory(opportunity=opportunity, opportunity_access=None, ward="w1")

    _approved_visit(opportunity, wa_hsd, datetime.date(2026, 3, 10))
    _approved_visit(
        opportunity,
        wa_ncwa,
        datetime.date(2026, 3, 10),
        deliver_unit=_deliver_unit(opportunity, NO_CHILDREN_WORK_AREA_UNIT_SLUG),
    )
    _approved_visit(opportunity, wa_overlap, datetime.date(2026, 3, 10))
    _approved_visit(opportunity, unassigned, datetime.date(2026, 3, 10))  # dropped: out of scope

    result = get_coverage_aggregates(opportunity, "ward", window=None)

    assert result["w1"]["WAs_visited"] == 5  # hsd, ncwa, inaccessible, request_inaccessible, overlap


def test_coverage_aggregates_buildings_covered_stays_hsd_only(opportunity):
    """Buildings_covered_in_WAs_visited sums only HSD-delivered areas, not the wider WAs_visited union."""
    access = _access(opportunity)
    wa_hsd = WorkAreaFactory(opportunity=opportunity, opportunity_access=access, ward="w1", building_count=10)
    WorkAreaFactory(
        opportunity=opportunity,
        opportunity_access=access,
        ward="w1",
        status=WorkAreaStatus.INACCESSIBLE,
        building_count=99,
    )
    _approved_visit(opportunity, wa_hsd, datetime.date(2026, 3, 10))

    result = get_coverage_aggregates(opportunity, "ward", window=None)

    assert result["w1"]["WAs_visited"] == 2  # both count toward the union
    assert result["w1"]["Buildings_covered_in_WAs_visited"] == 10  # only the HSD-delivered one


def test_coverage_aggregates_evc_reached_ignores_areas_with_no_target(opportunity):
    access = _access(opportunity)
    wa_reached = WorkAreaFactory(opportunity=opportunity, opportunity_access=access, ward="w1", expected_visit_count=2)
    WorkAreaFactory(opportunity=opportunity, opportunity_access=access, ward="w1", expected_visit_count=0)
    _approved_visit(opportunity, wa_reached, datetime.date(2026, 3, 10))
    _approved_visit(opportunity, wa_reached, datetime.date(2026, 3, 11))

    result = get_coverage_aggregates(opportunity, "ward", window=None)

    assert result["w1"]["WAs_evc_reached"] == 1


def test_coverage_aggregates_window_scopes_visits_but_not_status(opportunity):
    """The HSD/NCWA arm is windowed on visit_date; inaccessible status has no date to test, so it isn't."""
    access = _access(opportunity)
    WorkAreaFactory(opportunity=opportunity, opportunity_access=access, ward="w1", status=WorkAreaStatus.INACCESSIBLE)
    wa_hsd = WorkAreaFactory(opportunity=opportunity, opportunity_access=access, ward="w1", expected_visit_count=1)
    _approved_visit(opportunity, wa_hsd, datetime.date(2026, 3, 10))  # outside the window below

    window = (
        timezone.make_aware(datetime.datetime(2026, 4, 1)),
        timezone.make_aware(datetime.datetime(2026, 4, 30)),
    )
    result = get_coverage_aggregates(opportunity, "ward", window=window)

    assert result["w1"]["WAs_visited"] == 1  # the inaccessible area counts regardless of window
    assert result["w1"]["WAs_evc_reached"] == 0  # fully windowed: no HSD visit inside window


def test_target_aggregates_by_ward_excludes_excluded(opportunity):
    WorkAreaFactory(
        opportunity=opportunity,
        opportunity_access=_access(opportunity),
        ward="w1",
        status=WorkAreaStatus.VISITED,
        target_population=100,
        building_count=10,
        expected_visit_count=5,
    )
    WorkAreaFactory(
        opportunity=opportunity,
        opportunity_access=_access(opportunity),
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
        opportunity_access=_access(opportunity),
        ward="w2",
        status=WorkAreaStatus.NOT_VISITED,
        target_population=20,
        building_count=2,
        expected_visit_count=1,
    )

    result = get_target_aggregates(opportunity, "ward")

    assert result["w1"] == {
        "ward": "w1",
        "building_count": 14,
        "num_work_areas": 2,
        "expected_visit_total": 8,
    }
    assert result["w2"]["num_work_areas"] == 1


def test_target_aggregates_excludes_unassigned(opportunity):
    """An unassigned area has no FLW to do the work, so it leaves the denominator like an excluded one."""
    WorkAreaFactory(
        opportunity=opportunity,
        opportunity_access=_access(opportunity),
        ward="w1",
        building_count=10,
        expected_visit_count=5,
    )
    WorkAreaFactory(
        opportunity=opportunity, opportunity_access=None, ward="w1", building_count=99, expected_visit_count=99
    )

    result = get_target_aggregates(opportunity, "ward")

    assert result["w1"] == {"ward": "w1", "building_count": 10, "num_work_areas": 1, "expected_visit_total": 5}


def test_target_aggregates_by_wag_excludes_excluded(opportunity):
    group = WorkAreaGroupFactory(opportunity=opportunity)
    WorkAreaFactory(
        opportunity=opportunity,
        opportunity_access=_access(opportunity),
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
        "building_count": 10,
        "num_work_areas": 1,
        "expected_visit_total": 5,
    }


def _approved_visit(opportunity, work_area, when, **kwargs):
    """An approved visit, defaulting to the Service Delivery deliver unit unless one is passed."""
    kwargs.setdefault("deliver_unit", _deliver_unit(opportunity, SERVICE_DELIVERY_UNIT_SLUG))
    return UserVisitFactory(
        opportunity=opportunity,
        work_area=work_area,
        status=VisitValidationStatus.approved,
        visit_date=timezone.make_aware(datetime.datetime.combine(when, datetime.time(9, 0))),
        **kwargs,
    )


def _assigned_work_area(opportunity, status=WorkAreaStatus.NOT_VISITED):
    return WorkAreaFactory(opportunity=opportunity, opportunity_access=_access(opportunity), status=status)


def _deliver_unit(opportunity, slug):
    return DeliverUnitFactory(app=opportunity.deliver_app, slug=slug)


def test_in_scope_work_areas_drops_excluded_and_unassigned(opportunity):
    kept = [
        _assigned_work_area(opportunity, WorkAreaStatus.NOT_VISITED),
        _assigned_work_area(opportunity, WorkAreaStatus.VISITED),
        _assigned_work_area(opportunity, WorkAreaStatus.INACCESSIBLE),
        _assigned_work_area(opportunity, WorkAreaStatus.REQUEST_FOR_INACCESSIBLE),
    ]
    _assigned_work_area(opportunity, WorkAreaStatus.EXCLUDED)
    WorkAreaFactory(opportunity=opportunity, opportunity_access=None, status=WorkAreaStatus.UNASSIGNED)
    # In scope for its own opportunity, so only the opportunity filter keeps it out of this one.
    _assigned_work_area(opportunity=OpportunityFactory())

    assert set(in_scope_work_areas(opportunity)) == set(kept)


def test_annotate_visit_counts_counts_only_approved_service_delivery_visits(opportunity):
    work_area = _assigned_work_area(opportunity)
    empty_work_area = _assigned_work_area(opportunity)
    hsd = _deliver_unit(opportunity, SERVICE_DELIVERY_UNIT_SLUG)
    march = datetime.date(2026, 3, 10)
    _approved_visit(opportunity, work_area, march, deliver_unit=hsd)
    _approved_visit(opportunity, work_area, march, deliver_unit=hsd)
    # dropped: another deliver unit, and a service delivery still awaiting review
    _approved_visit(opportunity, work_area, march, deliver_unit=_deliver_unit(opportunity, "registration"))
    UserVisitFactory(
        opportunity=opportunity,
        work_area=work_area,
        deliver_unit=hsd,
        status=VisitValidationStatus.pending,
        visit_date=timezone.make_aware(datetime.datetime(2026, 3, 10, 9, 0)),
    )

    counts = {
        wa.pk: wa.hsd_count for wa in annotate_approved_visit_counts(in_scope_work_areas(opportunity), opportunity)
    }
    assert counts == {work_area.pk: 2, empty_work_area.pk: 0}


def test_annotate_visit_counts_adds_the_no_children_count_on_request(opportunity):
    work_area = _assigned_work_area(opportunity)
    march = datetime.date(2026, 3, 10)
    _approved_visit(opportunity, work_area, march, deliver_unit=_deliver_unit(opportunity, SERVICE_DELIVERY_UNIT_SLUG))
    _approved_visit(
        opportunity, work_area, march, deliver_unit=_deliver_unit(opportunity, NO_CHILDREN_WORK_AREA_UNIT_SLUG)
    )

    row = annotate_approved_visit_counts(in_scope_work_areas(opportunity), opportunity, ncwa=True).get(pk=work_area.pk)
    assert (row.hsd_count, row.ncwa_count) == (1, 1)


def test_annotate_visit_counts_window_filters_on_visit_date(opportunity):
    work_area = _assigned_work_area(opportunity)
    hsd = _deliver_unit(opportunity, SERVICE_DELIVERY_UNIT_SLUG)
    _approved_visit(opportunity, work_area, datetime.date(2026, 3, 10), deliver_unit=hsd)
    _approved_visit(opportunity, work_area, datetime.date(2026, 4, 10), deliver_unit=hsd)

    window = CoverageDateFilter(start=datetime.date(2026, 3, 1), end=datetime.date(2026, 3, 31)).window
    annotated = annotate_approved_visit_counts(in_scope_work_areas(opportunity), opportunity, window=window)
    assert annotated.get(pk=work_area.pk).hsd_count == 1


def test_visits_approved_overall_excludes_excluded_and_unapproved(opportunity):
    wa = WorkAreaFactory(
        opportunity=opportunity, opportunity_access=_access(opportunity), ward="w1", status=WorkAreaStatus.VISITED
    )
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


def test_visits_approved_excludes_unassigned(opportunity):
    """An unassigned area has no FLW to do the work, so its approved visits don't count either."""
    unassigned = WorkAreaFactory(opportunity=opportunity, opportunity_access=None, ward="w1")
    _approved_visit(opportunity, unassigned, datetime.date(2026, 3, 10))

    result = get_visits_approved_aggregates(opportunity, "ward", window=None)
    assert result.get("w1", {}).get("visits_approved", 0) == 0


def test_visits_approved_only_counts_service_delivery_unit(opportunity):
    """Only approved visits on the Service Delivery deliver unit count toward the tracker's visit columns."""
    wa = WorkAreaFactory(
        opportunity=opportunity, opportunity_access=_access(opportunity), ward="w1", status=WorkAreaStatus.VISITED
    )
    _approved_visit(opportunity, wa, datetime.date(2026, 3, 10))  # HSD, via the helper's default
    _approved_visit(
        opportunity, wa, datetime.date(2026, 3, 12), deliver_unit=_deliver_unit(opportunity, "registration")
    )

    result = get_visits_approved_aggregates(opportunity, "ward", window=None)
    assert result["w1"]["visits_approved"] == 1


def test_visits_approved_window_filters_visit_date(opportunity):
    wa = WorkAreaFactory(
        opportunity=opportunity, opportunity_access=_access(opportunity), ward="w1", status=WorkAreaStatus.VISITED
    )
    _approved_visit(opportunity, wa, datetime.date(2026, 3, 10))
    _approved_visit(opportunity, wa, datetime.date(2026, 4, 10))
    window = (datetime.date(2026, 3, 1), datetime.date(2026, 3, 31))
    assert get_visits_approved_aggregates(opportunity, "ward", window=window)["w1"]["visits_approved"] == 1


def test_build_ward_rows_merges_and_derives():
    target_aggregates = {
        "w1": {
            "ward": "w1",
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
        }
    }
    filtered_visits = {"w1": {"ward": "w1", "visits_approved": 20}}
    last_week_status = {
        "w1": {
            "ward": "w1",
            "WAs_visited": 1,
            "WAs_evc_reached": 0,
            "Buildings_covered_in_WAs_visited": 5,
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
    assert row["pct_WA_visited_to_pct_visits"] == 0.8  # 40 / 50
    assert row["WAs_visited_last_week"] == 1
    assert row["pct_WAs_visited_last_week"] == 10.0  # 1 / 10
    # last-week ratio = pct_WAs_visited_last_week / pct_visits_approved_last_week
    #                 = 10.0 / (5/40*100 = 12.5) = 0.8
    assert row["pct_WA_visited_to_pct_visits_last_week"] == 0.8


def test_build_ward_rows_zero_denominator_yields_none():
    target_aggregates = {
        "w1": {
            "ward": "w1",
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
        }
    }
    filtered_visits = {group.id: {"work_area_group_id": group.id, "visits_approved": 25}}
    last_week_status = {
        group.id: {
            "work_area_group_id": group.id,
            "WAs_visited": 2,
            "WAs_evc_reached": 1,
            "Buildings_covered_in_WAs_visited": 10,
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
    assert row["expected_visit_total"] == 50
    assert row["pct_visits_approved"] == 50.0  # 25 / 50
    assert row["pct_WAs_evc_reached"] == 25.0  # 3 / 12
    assert row["pct_WA_visited_to_pct_visits"] == 1.0  # (6/12=50) / (25/50=50)


def test_ward_saturation_goal_rolls_up_opportunity_wide():
    """Ward Saturation Goal is the Work Areas Done union rolled up opportunity-wide, not EVC-reached."""
    target_aggregates = {"w1": {"num_work_areas": 10}, "w2": {"num_work_areas": 10}}
    coverage_aggregates = {"w1": {"WAs_visited": 3}, "w2": {"WAs_visited": 2}}
    assert ward_saturation_goal(target_aggregates, coverage_aggregates) == 25.0  # 5 / 20 * 100


def test_ward_saturation_goal_zero_denominator_is_none():
    assert ward_saturation_goal({}, {}) is None


def test_report_exposes_header_ward_and_wag_rows(opportunity):
    group = WorkAreaGroupFactory(opportunity=opportunity, ward="w1", name="G1")
    wa = WorkAreaFactory(
        opportunity=opportunity,
        opportunity_access=_access(opportunity),
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
    """Ward Saturation Goal always uses the overall (unwindowed) slot, regardless of the page filter."""
    access = _access(opportunity)
    wa_done = WorkAreaFactory(opportunity=opportunity, opportunity_access=access, ward="w1")
    WorkAreaFactory(opportunity=opportunity, opportunity_access=access, ward="w1", status=WorkAreaStatus.NOT_VISITED)
    _approved_visit(opportunity, wa_done, datetime.date(2026, 3, 10))

    # An April window would exclude the March delivery from the *windowed* HSD count. The header
    # is cumulative, though: 1 of 2 work areas is done -> 50%, regardless of filter.
    april = CoverageDateFilter(start=datetime.date(2026, 4, 1), end=datetime.date(2026, 4, 30))
    assert CoverageProgressReport(opportunity, april).header()["ward_saturation_goal"] == 50.0


def test_custom_range_bypasses_filtered_cache_slot(opportunity):
    WorkAreaFactory(opportunity=opportunity, ward="w1", status=WorkAreaStatus.VISITED)
    custom = CoverageDateFilter(start=datetime.date(2026, 1, 1), end=datetime.date(2026, 1, 31))
    with patch("commcare_connect.microplanning.coverage_progress._filtered_overall_slot") as overall_slot:
        CoverageProgressReport(opportunity, custom).ward_rows()
        overall_slot.assert_not_called()


def test_slot_computes_once_then_serves_cache(opportunity):
    key = f"coverage:v2:static:opp={opportunity.id}"
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


def test_filterset_custom_range_maps_to_custom_window():
    result = _coverage_filter({"start": "2026-01-01", "end": "2026-01-31"}).to_date_filter()
    assert result == CoverageDateFilter(start=datetime.date(2026, 1, 1), end=datetime.date(2026, 1, 31))


@pytest.mark.parametrize(
    "data",
    [
        {"start": "2026-01-31", "end": "2026-01-01"},  # reversed
        {"start": "2026-01-01"},  # incomplete (no end)
        {"start": "not-a-date", "end": "2026-01-31"},  # invalid date
    ],
)
def test_filterset_invalid_custom_range_falls_back_to_overall(data):
    assert _coverage_filter(data).to_date_filter().is_overall is True


def test_filterset_single_date_is_a_validation_error():
    fs = _coverage_filter({"start": "2026-01-01"})
    assert fs.form.is_valid() is False
    assert "Select both a From and a To date to filter by a date range." in fs.form.non_field_errors()


def test_filterset_reversed_range_is_a_validation_error():
    fs = _coverage_filter({"start": "2026-01-31", "end": "2026-01-01"})
    assert fs.form.is_valid() is False
    assert "The From date must be on or before the To date." in fs.form.non_field_errors()


def test_filterset_export_querystring_carries_known_params_plus_export_args():
    qs = _coverage_filter({"start": "2026-01-01", "end": "2026-01-31", "bogus": "x"}).export_querystring(
        {"export": "csv", "table": "ward"}
    )
    assert qs == "start=2026-01-01&end=2026-01-31&export=csv&table=ward"


def test_filterset_export_querystring_drops_invalid_range():
    # A lone date doesn't resolve to a window, so the download link carries no date params.
    assert _coverage_filter({"start": "2026-01-01"}).export_querystring({"export": "csv", "table": "ward"}) == (
        "export=csv&table=ward"
    )
