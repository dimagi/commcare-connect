import pytest
from django.urls import reverse

from commcare_connect.audit.models import AuditReport, AuditReportEntry
from commcare_connect.audit.tests.factories import AuditReportEntryFactory, AuditReportFactory
from commcare_connect.flags.flag_names import WEEKLY_PERFORMANCE_REPORT
from commcare_connect.flags.models import Flag
from commcare_connect.opportunity.models import AssignedTask
from commcare_connect.opportunity.tests.factories import OpportunityAccessFactory, OpportunityFactory, TaskTypeFactory


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


# ---------------------------------------------------------------------------
# Detail view tests
# ---------------------------------------------------------------------------


def _entry(report, opp, flagged, reviewed=False):
    access = OpportunityAccessFactory(opportunity=opp, accepted=True)
    return AuditReportEntryFactory(
        audit_report=report,
        opportunity_access=access,
        flagged=flagged,
        reviewed=reviewed,
        results={
            "fake": {
                "value": 0.5 if flagged else 0.9,
                "has_sufficient_data": True,
                "in_range": not flagged,
                "label": "Fake",
            }
        },
    )


def _detail_url(audit_opp, report):
    return reverse(
        "opportunity:audit:audit_report_detail",
        kwargs={
            "org_slug": audit_opp.organization.slug,
            "opportunity_id": audit_opp.opportunity_id,
            "audit_report_id": report.audit_report_id,
        },
    )


@pytest.mark.django_db
def test_detail_splits_flagged_and_unflagged(client, program_manager_org_user_admin, audit_opp):
    client.force_login(program_manager_org_user_admin)
    report = AuditReportFactory(opportunity=audit_opp)

    flagged_entry = _entry(report, audit_opp, flagged=True)
    unflagged_entry = _entry(report, audit_opp, flagged=False)

    response = client.get(_detail_url(audit_opp, report))
    assert response.status_code == 200
    html = response.content.decode()
    # Both user names appear.
    assert flagged_entry.opportunity_access.user.name in html
    assert unflagged_entry.opportunity_access.user.name in html
    # Progress indicator "0 of 1 workers reviewed" — only flagged rows are counted.
    assert "0 of 1" in html


@pytest.mark.django_db
def test_detail_filter_limits_tables_server_side(client, program_manager_org_user_admin, audit_opp):
    client.force_login(program_manager_org_user_admin)
    report = AuditReportFactory(opportunity=audit_opp)

    alice_access = OpportunityAccessFactory(opportunity=audit_opp, accepted=True)
    alice_access.user.name = "Alice Smith"
    alice_access.user.save(update_fields=["name"])
    bob_access = OpportunityAccessFactory(opportunity=audit_opp, accepted=True)
    bob_access.user.name = "Bob Jones"
    bob_access.user.save(update_fields=["name"])

    for access in (alice_access, bob_access):
        AuditReportEntryFactory(
            audit_report=report,
            opportunity_access=access,
            flagged=True,
            results={"fake": {"value": 0.5, "has_sufficient_data": True, "in_range": False, "label": "Fake"}},
        )

    # htmx-style partial request with a filter.
    response = client.get(
        _detail_url(audit_opp, report),
        {"filter": "alice"},
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 200
    html = response.content.decode()
    assert "Alice Smith" in html
    assert "Bob Jones" not in html


@pytest.mark.django_db
def test_detail_404_when_flag_disabled(client, program_manager_org_user_admin, audit_opp):
    Flag.objects.get(name=WEEKLY_PERFORMANCE_REPORT).opportunities.remove(audit_opp)
    client.force_login(program_manager_org_user_admin)
    report = AuditReportFactory(opportunity=audit_opp)
    response = client.get(_detail_url(audit_opp, report))
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Task modal tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_task_modal_renders_task_types(client, program_manager_org_user_admin, audit_opp):
    client.force_login(program_manager_org_user_admin)
    report = AuditReportFactory(opportunity=audit_opp)
    entry = _entry(report, audit_opp, flagged=True)

    task_type = TaskTypeFactory(opportunity=audit_opp, name="Refresher Module A")

    url = reverse(
        "opportunity:audit:audit_report_task_modal",
        kwargs={
            "org_slug": audit_opp.organization.slug,
            "opportunity_id": audit_opp.opportunity_id,
            "audit_report_id": report.audit_report_id,
            "entry_id": entry.audit_report_entry_id,
        },
    )
    response = client.get(url)
    assert response.status_code == 200
    html = response.content.decode()
    assert task_type.name in html
    assert entry.opportunity_access.user.name in html


# ---------------------------------------------------------------------------
# Modal submit and complete-audit endpoint tests
# ---------------------------------------------------------------------------


def _action_url(audit_opp, report, entry):
    return reverse(
        "opportunity:audit:audit_report_task_action",
        kwargs={
            "org_slug": audit_opp.organization.slug,
            "opportunity_id": audit_opp.opportunity_id,
            "audit_report_id": report.audit_report_id,
            "entry_id": entry.audit_report_entry_id,
        },
    )


def _complete_url(audit_opp, report):
    return reverse(
        "opportunity:audit:audit_report_complete",
        kwargs={
            "org_slug": audit_opp.organization.slug,
            "opportunity_id": audit_opp.opportunity_id,
            "audit_report_id": report.audit_report_id,
        },
    )


@pytest.mark.django_db
def test_modal_submit_assigns_tasks(client, program_manager_org_user_admin, audit_opp):
    client.force_login(program_manager_org_user_admin)
    report = AuditReportFactory(opportunity=audit_opp)
    entry = _entry(report, audit_opp, flagged=True)
    task_type = TaskTypeFactory(opportunity=audit_opp, name="Refresher Module A")

    response = client.post(
        _action_url(audit_opp, report, entry),
        data={"action": "tasks_assigned", "task_type_ids": [str(task_type.pk)]},
    )
    assert response.status_code == 200
    entry.refresh_from_db()
    assert entry.reviewed is True
    assert entry.review_action == AuditReportEntry.ReviewAction.TASKS_ASSIGNED
    assert AssignedTask.objects.filter(task_type=task_type).count() == 1


@pytest.mark.django_db
def test_modal_submit_no_action(client, program_manager_org_user_admin, audit_opp):
    client.force_login(program_manager_org_user_admin)
    report = AuditReportFactory(opportunity=audit_opp)
    entry = _entry(report, audit_opp, flagged=True)
    TaskTypeFactory(opportunity=audit_opp, name="Refresher Module A")

    response = client.post(_action_url(audit_opp, report, entry), data={"action": "none"})
    assert response.status_code == 200
    entry.refresh_from_db()
    assert entry.reviewed is True
    assert entry.review_action == AuditReportEntry.ReviewAction.NONE
    assert AssignedTask.objects.count() == 0


@pytest.mark.django_db
def test_complete_audit_succeeds_when_all_reviewed(client, program_manager_org_user_admin, audit_opp):
    client.force_login(program_manager_org_user_admin)
    report = AuditReportFactory(opportunity=audit_opp)
    _entry(report, audit_opp, flagged=True, reviewed=True)

    response = client.post(_complete_url(audit_opp, report))
    assert response.status_code == 204
    report.refresh_from_db()
    assert report.status == AuditReport.Status.COMPLETED
    assert report.completed_by == program_manager_org_user_admin
    assert report.completed_date is not None


@pytest.mark.django_db
def test_complete_audit_blocked_when_flagged_unreviewed(client, program_manager_org_user_admin, audit_opp):
    client.force_login(program_manager_org_user_admin)
    report = AuditReportFactory(opportunity=audit_opp)
    _entry(report, audit_opp, flagged=True, reviewed=False)

    response = client.post(_complete_url(audit_opp, report))
    assert response.status_code == 400
    report.refresh_from_db()
    assert report.status == AuditReport.Status.PENDING


@pytest.mark.django_db
def test_modal_submit_rejects_already_reviewed(client, program_manager_org_user_admin, audit_opp):
    """Re-submitting a reviewed entry must not duplicate AssignedTasks."""
    client.force_login(program_manager_org_user_admin)
    report = AuditReportFactory(opportunity=audit_opp)
    entry = _entry(report, audit_opp, flagged=True, reviewed=True)
    task_type = TaskTypeFactory(opportunity=audit_opp, name="Refresher")

    response = client.post(
        _action_url(audit_opp, report, entry),
        data={"action": "tasks_assigned", "task_type_ids": [str(task_type.pk)]},
    )
    assert response.status_code == 400
    assert AssignedTask.objects.count() == 0


@pytest.mark.django_db
def test_modal_submit_rejects_cross_opportunity_task_type(
    client, program_manager_org_user_admin, audit_opp, opportunity
):
    """A submitted task_type_id from another opportunity must not be assigned."""
    client.force_login(program_manager_org_user_admin)
    report = AuditReportFactory(opportunity=audit_opp)
    entry = _entry(report, audit_opp, flagged=True)
    foreign_task_type = TaskTypeFactory(opportunity=opportunity, name="Foreign")

    response = client.post(
        _action_url(audit_opp, report, entry),
        data={"action": "tasks_assigned", "task_type_ids": [str(foreign_task_type.pk)]},
    )
    assert response.status_code == 200
    assert AssignedTask.objects.count() == 0
    entry.refresh_from_db()
    # Entry is still marked reviewed (the action ran; no foreign task types matched).
    assert entry.reviewed is True
