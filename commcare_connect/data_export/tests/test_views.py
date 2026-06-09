import datetime

import pytest
from django.contrib.auth.models import Permission
from django.urls import reverse
from django.utils import timezone
from django.utils.timezone import now

from commcare_connect.audit.tests.factories import AuditReportEntryFactory, AuditReportFactory
from commcare_connect.microplanning.tests.factories import WorkAreaFactory, WorkAreaGroupFactory
from commcare_connect.opportunity.models import LabsRecord
from commcare_connect.opportunity.tests.factories import AssignedTaskFactory, OpportunityAccessFactory, TaskTypeFactory
from commcare_connect.users.tests.factories import LLOEntityFactory, OrgWithUsersFactory


def _add_export_credentials(api_client, user):
    token, _ = user.oauth2_provider_accesstoken.get_or_create(
        token="export-token",
        scope="read write export",
        defaults={"expires": now() + datetime.timedelta(hours=1)},
    )
    api_client.credentials(**{**getattr(api_client, "_credentials", {}), "Authorization": f"Bearer {token}"})


def _add_v2_header(api_client):
    api_client.credentials(
        **{**getattr(api_client, "_credentials", {}), "HTTP_ACCEPT": "application/json; version=2.0"}
    )


@pytest.fixture
def v2_export_client(api_client, org_user_member):
    _add_export_credentials(api_client, org_user_member)
    _add_v2_header(api_client)
    return api_client


@pytest.mark.django_db
class TestTaskTypeDataView:
    def test_returns_task_list(self, v2_export_client, opportunity):
        task = TaskTypeFactory(opportunity=opportunity)
        url = reverse("data_export:task_type_data", kwargs={"opp_id": opportunity.id})
        response = v2_export_client.get(url)
        assert response.status_code == 200
        data = response.json()
        assert len(data["results"]) == 1
        result = data["results"][0]
        assert result["id"] == task.id
        assert result["name"] == task.name

    def test_returns_404_for_unauthorized_opportunity(self, api_client, opportunity, user):
        _add_export_credentials(api_client, user)
        _add_v2_header(api_client)
        url = reverse("data_export:task_type_data", kwargs={"opp_id": opportunity.id})
        response = api_client.get(url)
        assert response.status_code == 404


@pytest.mark.django_db
class TestAssignedTaskDataView:
    def test_returns_assigned_task_list(self, v2_export_client, opportunity):
        opp_access = OpportunityAccessFactory(opportunity=opportunity)
        task_type = TaskTypeFactory(opportunity=opportunity)
        assigned_task = AssignedTaskFactory(task_type=task_type, opportunity_access=opp_access)
        url = reverse("data_export:assigned_task_data", kwargs={"opp_id": opportunity.id})
        response = v2_export_client.get(url)
        assert response.status_code == 200
        data = response.json()
        assert len(data["results"]) == 1
        result = data["results"][0]
        assert result["id"] == assigned_task.id
        assert result["task_type"] == task_type.id
        assert result["task_type_name"] == task_type.name
        assert result["username"] == opp_access.user.username

    def test_returns_404_for_unauthorized_opportunity(self, api_client, opportunity, user):
        _add_export_credentials(api_client, user)
        _add_v2_header(api_client)
        url = reverse("data_export:assigned_task_data", kwargs={"opp_id": opportunity.id})
        response = api_client.get(url)
        assert response.status_code == 404


@pytest.mark.django_db
class TestWorkAreaGroupDataView:
    def test_returns_work_area_group_list(self, v2_export_client, opportunity):
        group = WorkAreaGroupFactory(opportunity=opportunity)
        url = reverse("data_export:work_area_group_data", kwargs={"opp_id": opportunity.id})
        response = v2_export_client.get(url)
        assert response.status_code == 200
        data = response.json()
        assert len(data["results"]) == 1
        result = data["results"][0]
        assert result["id"] == group.id
        assert result["name"] == group.name

    def test_returns_404_for_unauthorized_opportunity(self, api_client, opportunity, user):
        _add_export_credentials(api_client, user)
        _add_v2_header(api_client)
        url = reverse("data_export:work_area_group_data", kwargs={"opp_id": opportunity.id})
        response = api_client.get(url)
        assert response.status_code == 404


@pytest.mark.django_db
class TestWorkAreaDataView:
    def test_returns_work_area_list(self, v2_export_client, opportunity):
        group = WorkAreaGroupFactory(opportunity=opportunity)
        area = WorkAreaFactory(opportunity=opportunity, work_area_group=group)
        url = reverse("data_export:work_area_data", kwargs={"opp_id": opportunity.id})
        response = v2_export_client.get(url)
        assert response.status_code == 200
        data = response.json()
        assert len(data["results"]) == 1
        result = data["results"][0]
        assert result["id"] == area.id
        assert result["slug"] == area.slug
        assert result["work_area_group"] == group.id
        assert result["work_area_group_name"] == group.name
        assert result["centroid"]["type"] == "Point"
        assert result["centroid"]["coordinates"] == [area.centroid.x, area.centroid.y]
        assert result["boundary"]["type"] == "Polygon"
        assert result["boundary"]["coordinates"] == [[list(coord) for coord in ring] for ring in area.boundary.coords]

    def test_returns_404_for_unauthorized_opportunity(self, api_client, opportunity, user):
        _add_export_credentials(api_client, user)
        _add_v2_header(api_client)
        url = reverse("data_export:work_area_data", kwargs={"opp_id": opportunity.id})
        response = api_client.get(url)
        assert response.status_code == 404


@pytest.mark.django_db
class TestLLOEntityDataView:
    def test_returns_llo_entity_list(self, api_client, user):
        entity = LLOEntityFactory(short_name="TST")
        permission = Permission.objects.get(codename="workspace_entity_management_access")
        user.user_permissions.add(permission)
        _add_export_credentials(api_client, user)
        _add_v2_header(api_client)
        url = reverse("data_export:llo_entity_data")
        response = api_client.get(url)
        assert response.status_code == 200
        data = response.json()
        assert len(data["results"]) == 1
        result = data["results"][0]
        assert result["id"] == entity.id
        assert result["name"] == entity.name
        assert result["short_name"] == "TST"

    def test_requires_export_scope(self, api_client, user):
        _add_v2_header(api_client)
        url = reverse("data_export:llo_entity_data")
        response = api_client.get(url)
        assert response.status_code == 401

    def test_requires_entity_management_permission(self, api_client, user):
        _add_export_credentials(api_client, user)
        _add_v2_header(api_client)
        url = reverse("data_export:llo_entity_data")
        response = api_client.get(url)
        assert response.status_code == 404


@pytest.mark.django_db
class TestAuditReportDataView:
    def test_returns_audit_reports_for_opportunity(self, v2_export_client, opportunity):
        report = AuditReportFactory(opportunity=opportunity)
        url = reverse("data_export:audit_report_data", kwargs={"opp_id": opportunity.id})
        response = v2_export_client.get(url)
        assert response.status_code == 200
        data = response.json()
        assert len(data["results"]) == 1
        result = data["results"][0]
        assert result["id"] == report.id
        assert result["audit_report_id"] == str(report.audit_report_id)
        assert result["opportunity"] == opportunity.id
        assert result["period_start"] == report.period_start.isoformat()
        assert result["period_end"] == report.period_end.isoformat()
        assert result["status"] == report.status
        assert result["completed_by_username"] is None
        assert result["completed_date"] is None

    def test_includes_completed_metadata(self, v2_export_client, opportunity, org_user_member):
        completed_at = timezone.now()
        report = AuditReportFactory(
            opportunity=opportunity,
            status="completed",
            completed_by=org_user_member,
            completed_date=completed_at,
        )
        url = reverse("data_export:audit_report_data", kwargs={"opp_id": opportunity.id})
        response = v2_export_client.get(url)
        result = response.json()["results"][0]
        assert result["id"] == report.id
        assert result["status"] == "completed"
        assert result["completed_by_username"] == org_user_member.username
        assert result["completed_date"] is not None

    def test_excludes_reports_from_other_opportunities(self, v2_export_client, opportunity):
        AuditReportFactory(opportunity=opportunity)
        AuditReportFactory()  # different opportunity (factory creates one)
        url = reverse("data_export:audit_report_data", kwargs={"opp_id": opportunity.id})
        response = v2_export_client.get(url)
        assert len(response.json()["results"]) == 1

    def test_returns_404_for_unauthorized_opportunity(self, api_client, opportunity, user):
        _add_export_credentials(api_client, user)
        _add_v2_header(api_client)
        url = reverse("data_export:audit_report_data", kwargs={"opp_id": opportunity.id})
        response = api_client.get(url)
        assert response.status_code == 404


@pytest.mark.django_db
class TestAuditReportEntryDataView:
    def test_returns_entries_for_opportunity(self, v2_export_client, opportunity):
        report = AuditReportFactory(opportunity=opportunity)
        opp_access = OpportunityAccessFactory(opportunity=opportunity)
        entry = AuditReportEntryFactory(
            audit_report=report,
            opportunity_access=opp_access,
            results={"hello": "world"},
            flagged=True,
            reviewed=False,
        )
        url = reverse("data_export:audit_report_entry_data", kwargs={"opp_id": opportunity.id})
        response = v2_export_client.get(url)
        assert response.status_code == 200
        results = response.json()["results"]
        assert len(results) == 1
        result = results[0]
        assert result["id"] == entry.id
        assert result["audit_report_entry_id"] == str(entry.audit_report_entry_id)
        assert result["audit_report"] == report.id
        assert result["audit_report_uuid"] == str(report.audit_report_id)
        assert result["opportunity_access"] == opp_access.id
        assert result["username"] == opp_access.user.username
        assert result["results"] == {"hello": "world"}
        assert result["flagged"] is True
        assert result["reviewed"] is False

    def test_excludes_entries_from_other_opportunities(self, v2_export_client, opportunity):
        report = AuditReportFactory(opportunity=opportunity)
        AuditReportEntryFactory(audit_report=report)
        AuditReportEntryFactory()
        url = reverse("data_export:audit_report_entry_data", kwargs={"opp_id": opportunity.id})
        response = v2_export_client.get(url)
        assert len(response.json()["results"]) == 1

    def test_filter_by_audit_report_id_returns_matching_entries(self, v2_export_client, opportunity):
        report_a = AuditReportFactory(opportunity=opportunity)
        report_b = AuditReportFactory(opportunity=opportunity)
        entry_a = AuditReportEntryFactory(audit_report=report_a)
        AuditReportEntryFactory(audit_report=report_b)
        url = reverse("data_export:audit_report_entry_data", kwargs={"opp_id": opportunity.id})
        response = v2_export_client.get(f"{url}?audit_report_id={report_a.audit_report_id}")
        results = response.json()["results"]
        assert len(results) == 1
        assert results[0]["id"] == entry_a.id

    def test_filter_by_invalid_uuid_returns_400(self, v2_export_client, opportunity):
        url = reverse("data_export:audit_report_entry_data", kwargs={"opp_id": opportunity.id})
        response = v2_export_client.get(f"{url}?audit_report_id=not-a-uuid")
        assert response.status_code == 400

    def test_filter_by_report_from_other_opportunity_returns_empty(self, v2_export_client, opportunity):
        report_in_opp = AuditReportFactory(opportunity=opportunity)
        AuditReportEntryFactory(audit_report=report_in_opp)
        other_report = AuditReportFactory()
        AuditReportEntryFactory(audit_report=other_report)
        url = reverse("data_export:audit_report_entry_data", kwargs={"opp_id": opportunity.id})
        response = v2_export_client.get(f"{url}?audit_report_id={other_report.audit_report_id}")
        assert response.status_code == 200
        assert response.json()["results"] == []

    def test_returns_404_for_unauthorized_opportunity(self, api_client, opportunity, user):
        _add_export_credentials(api_client, user)
        _add_v2_header(api_client)
        url = reverse("data_export:audit_report_entry_data", kwargs={"opp_id": opportunity.id})
        response = api_client.get(url)
        assert response.status_code == 404


@pytest.mark.django_db
class TestLabsRecordDataViewAuthorization:
    LABS_RECORD_URL = reverse("data_export:labs_record_data")

    def test_delete_cross_org_record_by_bare_id_returns_404(self, organization, api_client, org_user_member):
        other_org = OrgWithUsersFactory()
        record = LabsRecord.objects.create(experiment="test", organization=other_org, type="test", data={})
        _add_export_credentials(api_client, org_user_member)
        response = api_client.delete(self.LABS_RECORD_URL, data=[{"id": record.id}], format="json")
        assert response.status_code == 404
        assert LabsRecord.objects.filter(pk=record.pk).exists(), "Cross-org record must not be deleted"

    def test_delete_own_org_record_by_bare_id_succeeds(self, organization, api_client, org_user_member):
        record = LabsRecord.objects.create(experiment="test", organization=organization, type="test", data={})
        _add_export_credentials(api_client, org_user_member)
        response = api_client.delete(self.LABS_RECORD_URL, data=[{"id": record.id}], format="json")
        assert response.status_code == 200
        assert not LabsRecord.objects.filter(pk=record.pk).exists()

    def test_delete_nonexistent_id_returns_404(self, api_client, org_user_member):
        _add_export_credentials(api_client, org_user_member)
        response = api_client.delete(self.LABS_RECORD_URL, data=[{"id": 999999}], format="json")
        assert response.status_code == 404

    def test_post_cross_org_record_by_bare_id_returns_404(self, organization, api_client, org_user_member):
        other_org = OrgWithUsersFactory()
        record = LabsRecord.objects.create(experiment="original", organization=other_org, type="test", data={})
        _add_export_credentials(api_client, org_user_member)
        response = api_client.post(
            self.LABS_RECORD_URL,
            data=[{"id": record.id, "experiment": "hijacked", "type": "x", "data": {}}],
            format="json",
        )
        assert response.status_code == 404
        record.refresh_from_db()
        assert record.experiment == "original", "Cross-org record must not be overwritten"
