import datetime
from unittest import mock

import pytest

from commcare_connect.audit import services as services_module
from commcare_connect.audit.models import AuditReport
from commcare_connect.audit.tasks import generate_audit_reports
from commcare_connect.audit.tests.factories import AuditReportFactory  # noqa: F401 (keeps factory importable)
from commcare_connect.flags.flag_names import WEEKLY_PERFORMANCE_REPORT
from commcare_connect.flags.models import Flag
from commcare_connect.opportunity.tests.factories import OpportunityFactory

MONDAY_2AM_UTC = datetime.datetime(2026, 4, 20, 2, 0, tzinfo=datetime.UTC)


@pytest.mark.django_db
@mock.patch("commcare_connect.audit.tasks.timezone.now", return_value=MONDAY_2AM_UTC)
def test_task_generates_reports_only_for_flagged_opportunities(mock_now):
    flagged_opp = OpportunityFactory()
    unflagged_opp = OpportunityFactory()  # noqa: F841

    flag, _ = Flag.objects.get_or_create(name=WEEKLY_PERFORMANCE_REPORT)
    flag.opportunities.add(flagged_opp)

    generate_audit_reports()

    assert AuditReport.objects.filter(opportunity=flagged_opp).count() == 1
    assert AuditReport.objects.filter(opportunity=unflagged_opp).count() == 0


@pytest.mark.django_db
@mock.patch("commcare_connect.audit.tasks.timezone.now", return_value=MONDAY_2AM_UTC)
def test_task_continues_after_single_opportunity_failure(mock_now, caplog):
    opp_ok = OpportunityFactory()
    opp_fail = OpportunityFactory()
    flag, _ = Flag.objects.get_or_create(name=WEEKLY_PERFORMANCE_REPORT)
    flag.opportunities.add(opp_ok, opp_fail)

    real_generate = services_module.generate_audit_report_for_opportunity

    def faulty(opportunity, period_start, period_end):
        if opportunity.pk == opp_fail.pk:
            raise RuntimeError("boom")
        return real_generate(opportunity, period_start, period_end)

    with mock.patch(
        "commcare_connect.audit.tasks.generate_audit_report_for_opportunity",
        side_effect=faulty,
    ):
        generate_audit_reports()

    assert AuditReport.objects.filter(opportunity=opp_ok).count() == 1
    assert AuditReport.objects.filter(opportunity=opp_fail).count() == 0
    assert any("boom" in rec.message or "boom" in str(rec.exc_info) for rec in caplog.records)
