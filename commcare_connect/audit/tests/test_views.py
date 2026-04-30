import pytest
from django.urls import reverse

from commcare_connect.audit.models import AuditReport
from commcare_connect.audit.tests.factories import AuditReportFactory
from commcare_connect.flags.flag_names import WEEKLY_PERFORMANCE_REPORT
from commcare_connect.flags.models import Flag
from commcare_connect.opportunity.tests.factories import OpportunityFactory


@pytest.fixture
def audit_opp(program_manager_org):
    opportunity = OpportunityFactory(organization=program_manager_org)
    flag, _ = Flag.objects.get_or_create(name=WEEKLY_PERFORMANCE_REPORT)
    flag.opportunities.add(opportunity)
    return opportunity


@pytest.mark.django_db
def test_list_view_shows_reports(client, program_manager_org_user_admin, audit_opp):
    client.force_login(program_manager_org_user_admin)
    report = AuditReportFactory(opportunity=audit_opp)

    url = reverse(
        "opportunity:audit:audit_report_list",
        kwargs={"org_slug": audit_opp.organization.slug, "opportunity_id": audit_opp.opportunity_id},
    )
    response = client.get(url)
    assert response.status_code == 200
    html = response.content.decode()
    # Audit ID rendered as a 1-based serial number, not the pk.
    assert "#1" in html
    # Period end rendered in the Date column.
    assert report.period_end.strftime("%b") in html


@pytest.mark.django_db
def test_list_view_header_counts(client, program_manager_org_user_admin, audit_opp):
    client.force_login(program_manager_org_user_admin)
    AuditReportFactory(opportunity=audit_opp, status=AuditReport.Status.PENDING)
    AuditReportFactory(opportunity=audit_opp, status=AuditReport.Status.PENDING)
    AuditReportFactory(opportunity=audit_opp, status=AuditReport.Status.COMPLETED)

    url = reverse(
        "opportunity:audit:audit_report_list",
        kwargs={"org_slug": audit_opp.organization.slug, "opportunity_id": audit_opp.opportunity_id},
    )
    response = client.get(url)
    assert response.status_code == 200
    ctx = response.context
    assert ctx["total_count"] == 3
    assert ctx["pending_count"] == 2
    assert ctx["completed_count"] == 1


@pytest.mark.django_db
def test_list_view_404_when_flag_disabled(client, program_manager_org_user_admin, audit_opp):
    # Disable the flag for this opportunity; the request should still be permitted
    # past the program-manager decorator but 404 from the flag-gating helper.
    Flag.objects.get(name=WEEKLY_PERFORMANCE_REPORT).opportunities.remove(audit_opp)
    client.force_login(program_manager_org_user_admin)

    url = reverse(
        "opportunity:audit:audit_report_list",
        kwargs={"org_slug": audit_opp.organization.slug, "opportunity_id": audit_opp.opportunity_id},
    )
    response = client.get(url)
    assert response.status_code == 404
