from types import SimpleNamespace

import pytest

from commcare_connect.audit.chc_indicators import WorkAreasRemaining
from commcare_connect.audit.models import AuditReport
from commcare_connect.audit.tables import AuditReportEntryTable, AuditReportTable
from commcare_connect.audit.tests.factories import AuditReportFactory
from commcare_connect.opportunity.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    "name,username,expected",
    [
        ("Jane Auditor", "jane", "Jane Auditor"),
        ("", "jane", "jane"),
    ],
)
def test_render_reviewer_falls_back_to_username(name, username, expected):
    user = UserFactory.build(name=name, username=username)
    assert AuditReportTable([]).render_reviewer(user) == expected


def test_reviewer_column_default_for_no_reviewer():
    assert AuditReportTable([]).base_columns["reviewer"].default == "—"


@pytest.mark.parametrize("is_descending,expected", [(False, ["amy", "Bob", "zach"]), (True, ["zach", "Bob", "amy"])])
def test_order_reviewer_uses_username_fallback(is_descending, expected):
    # name="" sorts by username ("amy"), so blank-name reviewers interleave with named ones.
    AuditReportFactory(completed_by=UserFactory(name="", username="amy"))
    AuditReportFactory(completed_by=UserFactory(name="Bob", username="zzz_bob"))
    AuditReportFactory(completed_by=UserFactory(name="", username="zach"))

    ordered, applied = AuditReportTable([]).order_reviewer(AuditReport.objects.all(), is_descending)

    assert applied is True
    assert [r.completed_by.name or r.completed_by.username for r in ordered] == expected


@pytest.mark.parametrize(
    "has_work_areas_remaining, shows_icon",
    [(True, False), (False, True), (None, False)],
    ids=["work-left", "none-left", "unknown"],
)
def test_worker_cell_marks_flws_with_no_work_areas_left(has_work_areas_remaining, shows_icon):
    # None means the entry predates the calculation, so it holds no such result.
    if has_work_areas_remaining is None:
        results = {}
    else:
        results = {WorkAreasRemaining.name: {"value": has_work_areas_remaining}}
    record = SimpleNamespace(results=results)

    icon = AuditReportEntryTable._no_work_areas_icon(record)

    # The tooltip, not the glyph: this should survive a change of icon.
    assert ("No Work Areas remaining" in icon) is shows_icon


def test_work_areas_remaining_gets_no_column_of_its_own():
    report = AuditReportFactory()
    columns_spec = [(WorkAreasRemaining.name, WorkAreasRemaining.label, ""), ("calc_a", "Calc A", "")]

    table = AuditReportEntryTable(
        [], opportunity=report.opportunity, report=report, columns_spec=columns_spec, org_slug="org"
    )

    assert WorkAreasRemaining.name not in table.columns.names()
    assert "calc_a" in table.columns.names()
