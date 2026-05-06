import datetime

import pytest

from commcare_connect.audit import calculations
from commcare_connect.audit.models import AuditReport, AuditReportEntry
from commcare_connect.audit.services import generate_audit_report_for_opportunity, period_for
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
    from commcare_connect.audit.calculations import AuditCalculation

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
            return value, 1

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


def test_period_for_monday_returns_previous_week():
    # Task fires Monday 02:00 UTC — we want the week that just ended.
    start, end = period_for(datetime.date(2026, 4, 20))  # Monday
    assert start == datetime.date(2026, 4, 13)  # Monday prior
    assert end == datetime.date(2026, 4, 19)  # Sunday prior


def test_period_for_sunday_returns_previous_week_not_current():
    # Even if called mid-week or on Sunday itself, we return the already-completed
    # Mon-Sun window, never one still in progress.
    start, end = period_for(datetime.date(2026, 4, 19))  # Sunday
    assert start == datetime.date(2026, 4, 6)
    assert end == datetime.date(2026, 4, 12)


def test_period_for_any_weekday_returns_same_previous_week():
    # Wednesday 2026-04-22 — previous Mon-Sun is still 2026-04-13..2026-04-19.
    start, end = period_for(datetime.date(2026, 4, 22))
    assert start == datetime.date(2026, 4, 13)
    assert end == datetime.date(2026, 4, 19)
