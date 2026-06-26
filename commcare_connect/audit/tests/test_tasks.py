import datetime
from unittest import mock

import pytest

from commcare_connect.audit import services as services_module
from commcare_connect.audit.models import AuditReport
from commcare_connect.audit.tasks import generate_audit_reports, send_new_audit_report_notifications
from commcare_connect.audit.tests.factories import AuditReportFactory
from commcare_connect.flags.flag_names import WEEKLY_PERFORMANCE_REPORT
from commcare_connect.flags.models import Flag
from commcare_connect.opportunity.tests.factories import OpportunityFactory
from commcare_connect.program.tests.factories import ProgramFactory
from commcare_connect.users.tests.factories import MembershipFactory, ProgramManagerOrganisationFactory

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


@pytest.mark.django_db
@mock.patch("commcare_connect.audit.tasks.send_mail_async")
def test_notifies_pm_admins_for_single_report(mock_send):
    pm_org = ProgramManagerOrganisationFactory()
    admin = MembershipFactory(organization=pm_org, role="admin").user
    opp = OpportunityFactory(program=ProgramFactory(organization=pm_org), name="Malaria Opp")
    report = AuditReportFactory(opportunity=opp)

    send_new_audit_report_notifications([report])

    assert mock_send.delay.call_count == 1
    kwargs = mock_send.delay.call_args.kwargs
    assert kwargs["recipient_list"] == [admin.email]
    assert "Malaria Opp" in kwargs["subject"]
    assert "Malaria Opp" in kwargs["message"]
    assert str(report.audit_report_id) in kwargs["message"]
    assert "2026-04-13" in kwargs["message"]


@pytest.mark.django_db
@mock.patch("commcare_connect.audit.tasks.send_mail_async")
def test_groups_multiple_reports_for_same_org_into_one_email(mock_send):
    pm_org = ProgramManagerOrganisationFactory()
    admin = MembershipFactory(organization=pm_org, role="admin").user
    program = ProgramFactory(organization=pm_org)
    r1 = AuditReportFactory(opportunity=OpportunityFactory(program=program, name="Opp One"))
    r2 = AuditReportFactory(opportunity=OpportunityFactory(program=program, name="Opp Two"))

    send_new_audit_report_notifications([r1, r2])

    assert mock_send.delay.call_count == 1
    kwargs = mock_send.delay.call_args.kwargs
    assert kwargs["recipient_list"] == [admin.email]
    assert kwargs["subject"] == "2 new audit reports are ready for review"
    assert "Opp One" in kwargs["message"]
    assert "Opp Two" in kwargs["message"]


@pytest.mark.django_db
@mock.patch("commcare_connect.audit.tasks.send_mail_async")
def test_sends_separate_email_per_pm_org(mock_send):
    org_a = ProgramManagerOrganisationFactory()
    admin_a = MembershipFactory(organization=org_a, role="admin").user
    org_b = ProgramManagerOrganisationFactory()
    admin_b = MembershipFactory(organization=org_b, role="admin").user
    r_a = AuditReportFactory(opportunity=OpportunityFactory(program=ProgramFactory(organization=org_a), name="A Opp"))
    r_b = AuditReportFactory(opportunity=OpportunityFactory(program=ProgramFactory(organization=org_b), name="B Opp"))

    send_new_audit_report_notifications([r_a, r_b])

    assert mock_send.delay.call_count == 2
    by_recipient = {call.kwargs["recipient_list"][0]: call.kwargs for call in mock_send.delay.call_args_list}
    assert set(by_recipient) == {admin_a.email, admin_b.email}
    assert "A Opp" in by_recipient[admin_a.email]["message"]
    assert "B Opp" not in by_recipient[admin_a.email]["message"]
    assert "B Opp" in by_recipient[admin_b.email]["message"]


@pytest.mark.django_db
@mock.patch("commcare_connect.audit.tasks.send_mail_async")
def test_excludes_non_admin_members(mock_send):
    pm_org = ProgramManagerOrganisationFactory()
    admin = MembershipFactory(organization=pm_org, role="admin").user
    MembershipFactory(organization=pm_org, role="member")
    MembershipFactory(organization=pm_org, role="viewer")
    report = AuditReportFactory(opportunity=OpportunityFactory(program=ProgramFactory(organization=pm_org)))

    send_new_audit_report_notifications([report])

    assert mock_send.delay.call_args.kwargs["recipient_list"] == [admin.email]


@pytest.mark.django_db
@mock.patch("commcare_connect.audit.tasks.send_mail_async")
def test_skips_report_when_opportunity_has_no_program(mock_send):
    report = AuditReportFactory(opportunity=OpportunityFactory(program=None))

    send_new_audit_report_notifications([report])

    mock_send.delay.assert_not_called()


@pytest.mark.django_db
@mock.patch("commcare_connect.audit.tasks.send_mail_async")
def test_no_email_when_pm_org_has_no_admins(mock_send):
    pm_org = ProgramManagerOrganisationFactory()
    MembershipFactory(organization=pm_org, role="member")
    report = AuditReportFactory(opportunity=OpportunityFactory(program=ProgramFactory(organization=pm_org)))

    send_new_audit_report_notifications([report])

    mock_send.delay.assert_not_called()


@pytest.mark.django_db
@mock.patch("commcare_connect.audit.tasks.send_mail_async")
def test_one_org_failure_does_not_block_others(mock_send, caplog):
    org_a = ProgramManagerOrganisationFactory()
    MembershipFactory(organization=org_a, role="admin")
    org_b = ProgramManagerOrganisationFactory()
    MembershipFactory(organization=org_b, role="admin")
    r_a = AuditReportFactory(opportunity=OpportunityFactory(program=ProgramFactory(organization=org_a)))
    r_b = AuditReportFactory(opportunity=OpportunityFactory(program=ProgramFactory(organization=org_b)))

    mock_send.delay.side_effect = [RuntimeError("boom"), None]

    send_new_audit_report_notifications([r_a, r_b])

    assert mock_send.delay.call_count == 2
    assert any("boom" in str(rec.exc_info) for rec in caplog.records)
