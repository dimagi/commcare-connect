import pytest
from django.urls import reverse

from commcare_connect.audit.models import AuditReport, AuditReportEntry
from commcare_connect.audit.tests.factories import AuditReportEntryFactory, AuditReportFactory
from commcare_connect.flags.flag_names import WEEKLY_PERFORMANCE_REPORT
from commcare_connect.flags.models import Flag
from commcare_connect.opportunity.models import AssignedTask
from commcare_connect.opportunity.tests.factories import (
    OpportunityAccessFactory,
    OpportunityFactory,
    TaskTypeFactory,
    UserFactory,
)
from commcare_connect.organization.models import UserOrganizationMembership
from commcare_connect.users.tests.factories import OrgWithUsersFactory


@pytest.fixture
def audit_opp(program_manager_org):
    opportunity = OpportunityFactory(organization=program_manager_org)
    flag, _ = Flag.objects.get_or_create(name=WEEKLY_PERFORMANCE_REPORT)
    flag.opportunities.add(opportunity)
    return opportunity


@pytest.fixture
def nm_audit_opp(managed_opportunity):
    flag, _ = Flag.objects.get_or_create(name=WEEKLY_PERFORMANCE_REPORT)
    flag.opportunities.add(managed_opportunity)
    return managed_opportunity


@pytest.mark.django_db
@pytest.mark.parametrize(
    "role, expected_status",
    [
        (UserOrganizationMembership.Role.ADMIN, 200),
        (UserOrganizationMembership.Role.MEMBER, 200),
        (UserOrganizationMembership.Role.VIEWER, 404),
    ],
)
def test_nm_audit_list_access_by_role(client, role, expected_status, nm_audit_opp):
    """Audit access for the network manager (the opportunity's delivery org) follows
    @org_member_required: members and admins are allowed, viewers are denied. The same
    decorator gates all audit views, so the role behaviour is asserted once here."""
    user = UserFactory()
    UserOrganizationMembership.objects.create(user=user, organization=nm_audit_opp.organization, role=role)
    client.force_login(user)
    AuditReportFactory(opportunity=nm_audit_opp)

    url = reverse(
        "opportunity:audit:audit_report_list",
        kwargs={"org_slug": nm_audit_opp.organization.slug, "opp_id": nm_audit_opp.opportunity_id},
    )
    assert client.get(url).status_code == expected_status


@pytest.mark.django_db
def test_unrelated_org_admin_cannot_access_audit(client, nm_audit_opp):
    """An admin of an org unrelated to the opportunity is denied both ways: via the
    opportunity's slug (not a member of that org) and via their own org's slug (the
    opportunity doesn't belong to it, so opportunity_required rejects it)."""
    other_org = OrgWithUsersFactory()
    other_admin = other_org.memberships.filter(role="admin").first().user
    client.force_login(other_admin)
    AuditReportFactory(opportunity=nm_audit_opp)

    via_real_slug = reverse(
        "opportunity:audit:audit_report_list",
        kwargs={"org_slug": nm_audit_opp.organization.slug, "opp_id": nm_audit_opp.opportunity_id},
    )
    assert client.get(via_real_slug).status_code == 404

    via_own_slug = reverse(
        "opportunity:audit:audit_report_list",
        kwargs={"org_slug": other_org.slug, "opp_id": nm_audit_opp.opportunity_id},
    )
    assert client.get(via_own_slug).status_code == 404


@pytest.mark.django_db
def test_nm_member_can_complete_review(client, org_user_member, nm_audit_opp):
    """AC: a network manager — here a non-admin member, the newly-granted case — can
    complete the FLW review. Business logic of completion is covered by the PM tests."""
    client.force_login(org_user_member)
    report = AuditReportFactory(opportunity=nm_audit_opp)
    access = OpportunityAccessFactory(opportunity=nm_audit_opp, accepted=True)
    AuditReportEntryFactory(
        audit_report=report,
        opportunity_access=access,
        flagged=True,
        reviewed=True,
        results={"fake": {"value": 0.5, "has_sufficient_data": True, "in_range": False, "label": "Fake"}},
    )

    url = reverse(
        "opportunity:audit:audit_report_complete",
        kwargs={
            "org_slug": nm_audit_opp.organization.slug,
            "opp_id": nm_audit_opp.opportunity_id,
            "audit_report_id": report.audit_report_id,
        },
    )
    response = client.post(url)
    assert response.status_code == 204
    report.refresh_from_db()
    assert report.status == AuditReport.Status.COMPLETED
    assert report.completed_by == org_user_member


@pytest.mark.django_db
def test_nm_member_can_assign_tasks(client, org_user_member, nm_audit_opp):
    """AC: a network manager — here a non-admin member — can assign audit tasks to an FLW."""
    client.force_login(org_user_member)
    report = AuditReportFactory(opportunity=nm_audit_opp)
    access = OpportunityAccessFactory(opportunity=nm_audit_opp, accepted=True)
    entry = AuditReportEntryFactory(
        audit_report=report,
        opportunity_access=access,
        flagged=True,
        results={"fake": {"value": 0.5, "has_sufficient_data": True, "in_range": False, "label": "Fake"}},
    )
    task_type = TaskTypeFactory(opportunity=nm_audit_opp, name="Refresher Module A")

    url = reverse(
        "opportunity:audit:audit_report_task_action",
        kwargs={
            "org_slug": nm_audit_opp.organization.slug,
            "opp_id": nm_audit_opp.opportunity_id,
            "audit_report_id": report.audit_report_id,
            "entry_id": entry.audit_report_entry_id,
        },
    )
    response = client.post(url, data={"action": "tasks_assigned", "task_type_ids": [str(task_type.pk)]})
    assert response.status_code == 200
    entry.refresh_from_db()
    assert entry.reviewed is True
    assert AssignedTask.objects.filter(task_type=task_type).count() == 1


@pytest.mark.django_db
def test_list_view_shows_reports(client, program_manager_org_user_admin, audit_opp):
    client.force_login(program_manager_org_user_admin)
    report = AuditReportFactory(opportunity=audit_opp)

    url = reverse(
        "opportunity:audit:audit_report_list",
        kwargs={"org_slug": audit_opp.organization.slug, "opp_id": audit_opp.opportunity_id},
    )
    response = client.get(url)
    assert response.status_code == 200
    html = response.content.decode()
    # Numbering column header
    assert ">#</span>" in html
    # Generation Date column header and date_created value are rendered.
    assert "Generation Date" in html
    assert report.date_created.strftime("%b") in html


@pytest.mark.django_db
def test_list_view_header_counts(client, program_manager_org_user_admin, audit_opp):
    client.force_login(program_manager_org_user_admin)
    AuditReportFactory(opportunity=audit_opp, status=AuditReport.Status.PENDING)
    AuditReportFactory(opportunity=audit_opp, status=AuditReport.Status.PENDING)
    AuditReportFactory(opportunity=audit_opp, status=AuditReport.Status.COMPLETED)

    url = reverse(
        "opportunity:audit:audit_report_list",
        kwargs={"org_slug": audit_opp.organization.slug, "opp_id": audit_opp.opportunity_id},
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
        kwargs={"org_slug": audit_opp.organization.slug, "opp_id": audit_opp.opportunity_id},
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
            "opp_id": audit_opp.opportunity_id,
            "audit_report_id": report.audit_report_id,
        },
    )


@pytest.mark.django_db
def test_detail_lists_all_workers_in_one_table(client, program_manager_org_user_admin, audit_opp):
    client.force_login(program_manager_org_user_admin)
    report = AuditReportFactory(opportunity=audit_opp)

    flagged_entry = _entry(report, audit_opp, flagged=True)
    unflagged_entry = _entry(report, audit_opp, flagged=False)

    response = client.get(_detail_url(audit_opp, report))
    assert response.status_code == 200
    html = response.content.decode()
    # Both flagged and no-action workers appear in the single merged table.
    rendered_rows = [e.opportunity_access.user.name for e in response.context["table"].page.object_list.data]
    assert flagged_entry.opportunity_access.user.name in rendered_rows
    assert unflagged_entry.opportunity_access.user.name in rendered_rows
    # The flagged worker (still needing review) is ordered ahead of the no-action one.
    assert rendered_rows[0] == flagged_entry.opportunity_access.user.name
    # Progress indicator "0 of 1 workers reviewed" — only flagged rows are counted.
    assert "0 of 1" in html


@pytest.mark.django_db
def test_detail_sorts_by_calculation_column(client, program_manager_org_user_admin, audit_opp):
    client.force_login(program_manager_org_user_admin)
    report = AuditReportFactory(opportunity=audit_opp)

    def _scored_entry(name, value):
        access = OpportunityAccessFactory(opportunity=audit_opp, accepted=True)
        access.user.name = name
        access.user.save(update_fields=["name"])
        return AuditReportEntryFactory(
            audit_report=report,
            opportunity_access=access,
            flagged=False,
            results={"fake": {"value": value, "has_sufficient_data": True, "in_range": True, "label": "Fake"}},
        )

    _scored_entry("High", 0.9)
    _scored_entry("Low", 0.1)
    _scored_entry("Mid", 0.5)

    response = client.get(_detail_url(audit_opp, report), {"sort": "fake"})
    assert response.status_code == 200
    rendered_rows = [e.opportunity_access.user.name for e in response.context["table"].page.object_list.data]
    assert rendered_rows == ["Low", "Mid", "High"]

    response = client.get(_detail_url(audit_opp, report), {"sort": "-fake"})
    rendered_rows = [e.opportunity_access.user.name for e in response.context["table"].page.object_list.data]
    assert rendered_rows == ["High", "Mid", "Low"]


@pytest.mark.django_db
def test_detail_filter_limits_table_server_side(client, program_manager_org_user_admin, audit_opp):
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

    # htmx-style partial request filtered to a single selected worker.
    response = client.get(
        _detail_url(audit_opp, report),
        {"worker": str(alice_access.pk)},
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 200
    rendered_rows = [e.opportunity_access.user.name for e in response.context["table"].page.object_list.data]
    assert rendered_rows == ["Alice Smith"]
    # Both workers remain available as filter options.
    option_names = [name for _, name in response.context["worker_filter_choices"]]
    assert "Alice Smith" in option_names
    assert "Bob Jones" in option_names


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
def test_task_modal_renders(client, program_manager_org_user_admin, audit_opp):
    client.force_login(program_manager_org_user_admin)
    report = AuditReportFactory(opportunity=audit_opp)
    access = OpportunityAccessFactory(opportunity=audit_opp, accepted=True)
    entry = AuditReportEntryFactory(
        audit_report=report,
        opportunity_access=access,
        flagged=True,
        results={
            "ratio": {"value": 0.564356, "has_sufficient_data": True, "in_range": False, "label": "Ratio"},
            "female": {
                "value": 56.44543,
                "has_sufficient_data": True,
                "in_range": False,
                "label": "Female %",
                "numerator": 56,
                "denominator": 100,
            },
        },
    )
    task_type = TaskTypeFactory(opportunity=audit_opp, name="Refresher Module A")

    url = reverse(
        "opportunity:audit:audit_report_task_modal",
        kwargs={
            "org_slug": audit_opp.organization.slug,
            "opp_id": audit_opp.opportunity_id,
            "audit_report_id": report.audit_report_id,
            "entry_id": entry.audit_report_entry_id,
        },
    )
    response = client.get(url)
    assert response.status_code == 200
    html = response.content.decode()

    # Task types and worker are shown
    assert task_type.name in html
    assert access.user.name in html

    # Out-of-range results
    assert "0.56" in html
    assert "0.564356" not in html
    assert "56%" in html
    assert "56.44543" not in html


# ---------------------------------------------------------------------------
# Modal submit and complete-audit endpoint tests
# ---------------------------------------------------------------------------


def _action_url(audit_opp, report, entry):
    return reverse(
        "opportunity:audit:audit_report_task_action",
        kwargs={
            "org_slug": audit_opp.organization.slug,
            "opp_id": audit_opp.opportunity_id,
            "audit_report_id": report.audit_report_id,
            "entry_id": entry.audit_report_entry_id,
        },
    )


def _complete_url(audit_opp, report):
    return reverse(
        "opportunity:audit:audit_report_complete",
        kwargs={
            "org_slug": audit_opp.organization.slug,
            "opp_id": audit_opp.opportunity_id,
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
def test_modal_submit_returns_400_when_task_already_assigned(client, program_manager_org_user_admin, audit_opp):
    """Re-assigning an already-assigned task type returns 400 with a user-friendly message."""
    client.force_login(program_manager_org_user_admin)
    report = AuditReportFactory(opportunity=audit_opp)
    entry = _entry(report, audit_opp, flagged=True)
    task_type = TaskTypeFactory(opportunity=audit_opp, name="Refresher Module A")

    # First submission — succeeds.
    client.post(
        _action_url(audit_opp, report, entry),
        data={"action": "tasks_assigned", "task_type_ids": [str(task_type.pk)]},
    )
    assert AssignedTask.objects.filter(task_type=task_type).count() == 1

    # Reset entry so the guard checks pass, then try to assign the same task again.
    entry.reviewed = False
    entry.review_action = None
    entry.save(update_fields=["reviewed", "review_action"])

    response = client.post(
        _action_url(audit_opp, report, entry),
        data={"action": "tasks_assigned", "task_type_ids": [str(task_type.pk)]},
    )
    assert response.status_code == 400
    assert "already assigned" in response.content.decode()
    assert "not completed" in response.content.decode()
    # No duplicate task created.
    assert AssignedTask.objects.filter(task_type=task_type).count() == 1


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


@pytest.mark.django_db
def test_export_streams_worker_filtered_csv(client, program_manager_org_user_admin, audit_opp):
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

    url = reverse(
        "opportunity:audit:export_audit_report",
        kwargs={
            "org_slug": audit_opp.organization.slug,
            "opp_id": audit_opp.opportunity_id,
            "audit_report_id": report.audit_report_id,
        },
    )
    response = client.get(url, {"worker": str(alice_access.pk)})

    assert response.status_code == 200
    assert response["Content-Type"] == "text/csv"
    assert "attachment" in response["Content-Disposition"]
    assert ".csv" in response["Content-Disposition"]
    body = b"".join(response.streaming_content).decode()
    assert "Fake" in body  # header column
    assert "Alice Smith" in body
    assert "Bob Jones" not in body  # worker filter applied


@pytest.mark.django_db
def test_export_404_when_flag_disabled(client, program_manager_org_user_admin, audit_opp):
    Flag.objects.get(name=WEEKLY_PERFORMANCE_REPORT).opportunities.remove(audit_opp)
    client.force_login(program_manager_org_user_admin)
    report = AuditReportFactory(opportunity=audit_opp)

    url = reverse(
        "opportunity:audit:export_audit_report",
        kwargs={
            "org_slug": audit_opp.organization.slug,
            "opp_id": audit_opp.opportunity_id,
            "audit_report_id": report.audit_report_id,
        },
    )
    response = client.get(url)
    assert response.status_code == 404
