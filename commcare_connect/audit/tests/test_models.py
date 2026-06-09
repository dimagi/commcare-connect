import datetime

import pytest

from commcare_connect.audit.models import AuditReport, AuditReportEntry
from commcare_connect.audit.tests.factories import AuditReportFactory
from commcare_connect.opportunity.tests.factories import OpportunityAccessFactory


@pytest.mark.django_db
def test_audit_report_defaults(opportunity):
    report = AuditReport.objects.create(
        opportunity=opportunity,
        period_start=datetime.date(2026, 4, 13),
        period_end=datetime.date(2026, 4, 19),
    )
    assert report.status == AuditReport.Status.PENDING
    assert report.completed_by is None
    assert report.completed_date is None
    assert report.audit_report_id is not None


@pytest.mark.django_db
def test_audit_report_entry_defaults():
    access = OpportunityAccessFactory(accepted=True)
    report = AuditReportFactory(opportunity=access.opportunity)
    entry = AuditReportEntry.objects.create(
        audit_report=report,
        opportunity_access=access,
        results={
            "example_calc": {
                "value": 0.5,
                "has_sufficient_data": True,
                "in_range": False,
                "label": "Example",
            }
        },
        flagged=True,
    )
    assert entry.reviewed is False
    assert entry.review_action is None
    assert entry.flagged is True
    assert entry.results["example_calc"]["value"] == 0.5
