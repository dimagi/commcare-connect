import pytest

from commcare_connect.audit.models import AuditReport
from commcare_connect.audit.tables import AuditReportTable
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
