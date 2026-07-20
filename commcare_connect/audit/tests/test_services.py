import datetime
from types import SimpleNamespace

import pytest

from commcare_connect.audit import calculations
from commcare_connect.audit.models import AuditReport, AuditReportEntry
from commcare_connect.audit.services import (
    _format_reference_range,
    entries_for_export,
    generate_audit_report_for_opportunity,
    period_for,
    stream_audit_report_csv,
)
from commcare_connect.audit.tests.factories import AuditReportFactory
from commcare_connect.opportunity.tests.factories import OpportunityAccessFactory


@pytest.fixture
def isolated_registry():
    """Replace the calculation registry with a single known calculation."""
    original = list(calculations._REGISTRY)
    calculations._REGISTRY.clear()
    yield
    calculations._REGISTRY[:] = original


@pytest.fixture
def fixed_calc(isolated_registry):
    from commcare_connect.audit.calculations import AuditCalculation, Measurement

    state = {"call_count": 0}

    class FakeCalc(AuditCalculation):
        name = "fake"
        label = "Fake"
        min_sample_size = 1
        lower_bound = 0.5

        def compute(self, opportunity_access, period_start, period_end):
            state["call_count"] += 1
            # Even pk → value=1.0 (in range); odd pk → value=0.0 (out of range).
            value = 1.0 if opportunity_access.pk % 2 == 0 else 0.0
            return Measurement(value, 1)

    calculations._REGISTRY.append(FakeCalc())
    return state


@pytest.mark.django_db
def test_generate_report_creates_entries_for_active_accesses(opportunity, fixed_calc):
    # Active/accepted access → gets an entry.
    accepted = OpportunityAccessFactory(opportunity=opportunity, accepted=True)
    # Suspended access → skipped.
    OpportunityAccessFactory(opportunity=opportunity, accepted=True, suspended=True)
    # Not-accepted access → skipped.
    OpportunityAccessFactory(opportunity=opportunity, accepted=False)

    report = generate_audit_report_for_opportunity(
        opportunity,
        period_start=datetime.date(2026, 4, 13),
        period_end=datetime.date(2026, 4, 19),
    )

    assert isinstance(report, AuditReport)
    assert report.status == AuditReport.Status.PENDING
    entries = AuditReportEntry.objects.filter(audit_report=report)
    assert entries.count() == 1
    entry = entries.get()
    assert entry.opportunity_access_id == accepted.id
    assert "fake" in entry.results
    assert entry.results["fake"]["label"] == "Fake"
    # flagged mirrors the fake calc's in_range result.
    assert entry.flagged == (accepted.pk % 2 != 0)


@pytest.mark.django_db
def test_generate_report_with_no_active_accesses(opportunity, fixed_calc):
    report = generate_audit_report_for_opportunity(
        opportunity,
        period_start=datetime.date(2026, 4, 13),
        period_end=datetime.date(2026, 4, 19),
    )
    assert AuditReport.objects.filter(pk=report.pk).exists()
    assert AuditReportEntry.objects.filter(audit_report=report).count() == 0


@pytest.mark.parametrize(
    "today, expected_start, expected_end",
    [
        # Task fires Monday 02:00 UTC — we want the week that just ended.
        (datetime.date(2026, 4, 20), datetime.date(2026, 4, 13), datetime.date(2026, 4, 19)),
        # On Sunday itself, return the already-completed Mon-Sun window, never one still in progress.
        (datetime.date(2026, 4, 19), datetime.date(2026, 4, 6), datetime.date(2026, 4, 12)),
        # Mid-week Wednesday — previous Mon-Sun is still 2026-04-13..2026-04-19.
        (datetime.date(2026, 4, 22), datetime.date(2026, 4, 13), datetime.date(2026, 4, 19)),
    ],
    ids=["monday", "sunday", "wednesday"],
)
def test_period_for_returns_previous_completed_week(today, expected_start, expected_end):
    start, end = period_for(today)
    assert start == expected_start
    assert end == expected_end


@pytest.mark.django_db
def test_entries_for_export_filters_by_selected_workers(make_audit_entry):
    report = AuditReportFactory()
    entries = {name: make_audit_entry(report, name, 1) for name in ("Bob", "Bobby", "Carol")}

    selected = [entries["Bob"].opportunity_access_id, entries["Bobby"].opportunity_access_id]
    rows = entries_for_export(report, selected_workers=selected)

    worker_names = {entry.opportunity_access.user.name for entry in rows}
    assert worker_names == {"Bob", "Bobby"}


@pytest.mark.django_db
def test_stream_audit_report_csv_outputs_header_and_rows(make_audit_entry):
    report = AuditReportFactory()
    make_audit_entry(report, "Bob", 0.5, in_range=False)  # out-of-range still exports raw value
    make_audit_entry(report, "Ann", None, has_data=False)  # insufficient data -> "N/A"

    lines = "".join(stream_audit_report_csv(report)).splitlines()

    assert lines[0] == "Connect Worker,Calc A"
    assert "Ann,N/A" in lines
    assert "Bob,0.5" in lines


@pytest.mark.django_db
def test_stream_audit_report_csv_applies_selected_workers(make_audit_entry):
    report = AuditReportFactory()
    entries = {name: make_audit_entry(report, name, 1) for name in ("Bob", "Bobby", "Carol")}

    csv_text = "".join(stream_audit_report_csv(report, selected_workers=[entries["Carol"].opportunity_access_id]))

    assert "Carol" in csv_text
    assert "Bob" not in csv_text


@pytest.mark.parametrize(
    "lower, upper, expected",
    [
        (0.5, 1.0, "0.5 - 1.0"),
        (0.5, None, ">= 0.5"),
        (None, 1.0, "<= 1.0"),
        (None, None, ""),
    ],
)
def test_format_reference_range(lower, upper, expected):
    calc = SimpleNamespace(lower_bound=lower, upper_bound=upper)
    assert _format_reference_range(calc) == expected


@pytest.mark.django_db
def test_stream_audit_report_csv_appends_reference_range_to_header(isolated_registry, make_audit_entry):
    @calculations.register_calculation
    class RangeCalc(calculations.AuditCalculation):
        name = "calc_a"
        label = "Calc A"
        lower_bound = 0.5
        upper_bound = 1.0

        def compute(self, opportunity_access, period_start, period_end):
            return 0.5, 1

    report = AuditReportFactory()
    make_audit_entry(report, "Bob", 0.5)

    header = "".join(stream_audit_report_csv(report)).splitlines()[0]

    assert header == "Connect Worker,Calc A (0.5 - 1.0)"
