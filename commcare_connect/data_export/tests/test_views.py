import datetime
import json
from unittest import mock

import pytest
from django.contrib.auth.models import Permission
from django.core.files.base import ContentFile
from django.core.files.storage import storages
from django.urls import reverse
from django.utils import timezone
from django.utils.timezone import now

from commcare_connect.audit.tests.factories import AuditReportEntryFactory, AuditReportFactory
from commcare_connect.data_export.views import WorkAreaBulkCreateView
from commcare_connect.flags.flag_names import MICROPLANNING
from commcare_connect.flags.models import Flag
from commcare_connect.microplanning.models import WorkArea, WorkAreaGroup, WorkAreaStatus
from commcare_connect.microplanning.tests.factories import (
    ImplementationAreaFactory,
    WorkAreaFactory,
    WorkAreaGroupFactory,
)
from commcare_connect.microplanning.tests.test_views import BaseMicroplanningFlagTest
from commcare_connect.opportunity.models import LabsRecord
from commcare_connect.opportunity.tests.factories import (
    AssignedTaskFactory,
    BlobMetaFactory,
    OpportunityAccessFactory,
    OpportunityFactory,
    TaskTypeFactory,
    UserVisitFactory,
)
from commcare_connect.users.tests.factories import LLOEntityFactory, OrgWithUsersFactory
from commcare_connect.utils.commcarehq_api import CommCareHQAPIException


def _patch_json(client, url, payload):
    return client.patch(url, data=json.dumps(payload), content_type="application/json")


def _post_json(client, url, payload):
    return client.post(url, data=json.dumps(payload), content_type="application/json")


def _add_export_credentials(api_client, user):
    token, _ = user.oauth2_provider_accesstoken.get_or_create(
        token="export-token",
        scope="read write export",
        defaults={"expires": now() + datetime.timedelta(hours=1)},
    )
    api_client.credentials(**{**getattr(api_client, "_credentials", {}), "Authorization": f"Bearer {token}"})


def _add_export_only_credentials(api_client, user):
    """Grants export scope but NOT write — used to assert write endpoints reject it."""
    token, _ = user.oauth2_provider_accesstoken.get_or_create(
        token="export-only-token",
        scope="read export",
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


@pytest.fixture
def v2_write_client(api_client, org_user_admin):
    """Write endpoints additionally require org-admin (mirroring org_admin_required on the
    equivalent htmx views), so member-level v2_export_client isn't enough for them."""
    _add_export_credentials(api_client, org_user_admin)
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


@pytest.mark.django_db
class TestImageView:
    def test_returns_image_bytes(self, api_client, opportunity, org_user_member):
        visit = UserVisitFactory(opportunity=opportunity)
        blob_meta = BlobMetaFactory(parent_id=visit.xform_id, content_type="image/jpeg")
        storages["default"].save(blob_meta.blob_id, ContentFile(b"imagebytes"))
        _add_export_credentials(api_client, org_user_member)
        url = reverse("data_export:image_export", kwargs={"opp_id": opportunity.id})
        response = api_client.get(url, {"blob_id": blob_meta.blob_id})
        assert response.status_code == 200
        assert b"".join(response.streaming_content) == b"imagebytes"

    def test_non_member_returns_404(self, api_client, opportunity, user):
        visit = UserVisitFactory(opportunity=opportunity)
        blob_meta = BlobMetaFactory(parent_id=visit.xform_id, content_type="image/jpeg")
        _add_export_credentials(api_client, user)
        url = reverse("data_export:image_export", kwargs={"opp_id": opportunity.id})
        response = api_client.get(url, {"blob_id": blob_meta.blob_id})
        assert response.status_code == 404


@pytest.mark.django_db
class TestAttachmentSignedUrlView:
    def _url(self, opportunity):
        return reverse("data_export:attachment_signed_url", kwargs={"opp_id": opportunity.id})

    def test_requires_export_scope(self, api_client, opportunity):
        response = api_client.get(self._url(opportunity), {"blob_id": "any"})
        assert response.status_code == 401

    def test_non_member_returns_404(self, api_client, opportunity, user):
        visit = UserVisitFactory(opportunity=opportunity)
        blob_meta = BlobMetaFactory(parent_id=visit.xform_id)
        _add_export_credentials(api_client, user)
        response = api_client.get(self._url(opportunity), {"blob_id": blob_meta.blob_id})
        assert response.status_code == 404

    def test_foreign_org_blob_returns_404(self, api_client, opportunity, org_user_member):
        foreign_visit = UserVisitFactory(opportunity__organization=OrgWithUsersFactory())
        blob_meta = BlobMetaFactory(parent_id=foreign_visit.xform_id)
        _add_export_credentials(api_client, org_user_member)
        response = api_client.get(self._url(opportunity), {"blob_id": blob_meta.blob_id})
        assert response.status_code == 404

    def test_returns_501_when_no_s3_backend(self, api_client, opportunity, org_user_member):
        visit = UserVisitFactory(opportunity=opportunity)
        blob_meta = BlobMetaFactory(parent_id=visit.xform_id)
        _add_export_credentials(api_client, org_user_member)
        # The test environment uses FileSystemStorage, which cannot produce a portable URL.
        response = api_client.get(self._url(opportunity), {"blob_id": blob_meta.blob_id})
        assert response.status_code == 501

    def test_returns_signed_url(self, api_client, opportunity, org_user_member):
        visit = UserVisitFactory(opportunity=opportunity)
        blob_meta = BlobMetaFactory(parent_id=visit.xform_id)
        _add_export_credentials(api_client, org_user_member)
        with (
            mock.patch("commcare_connect.data_export.views._default_storage_supports_signed_urls", return_value=True),
            mock.patch("commcare_connect.data_export.views._get_attachment_signed_url", return_value="https://signed"),
        ):
            response = api_client.get(self._url(opportunity), {"blob_id": blob_meta.blob_id})
        assert response.status_code == 200
        assert response.json() == {"attachment_signed_url": "https://signed"}


class _FakeSignedStorage:
    """Storage stand-in that records how ``url()`` is invoked.

    Used because django-storages (the real S3 backend) is a production-only dependency and
    is absent from the test environment. ``location`` is a class default; a per-instance
    override models config a *resolved* storage carries, so this verifies the copy retains
    it rather than falling back to bare class defaults.
    """

    location = "class-default"

    def __init__(self):
        self.querystring_auth = False

    def url(self, name, expire, http_method):
        return f"https://signed/{self.location}/{name}?auth={self.querystring_auth}&method={http_method}"


def test_get_attachment_signed_url_preserves_resolved_storage_config():
    from commcare_connect.data_export.views import _get_attachment_signed_url

    storage = _FakeSignedStorage()
    storage.location = "configured/prefix"  # instance-level config, e.g. a STORAGES OPTIONS override
    with mock.patch("commcare_connect.data_export.views.storages", {"default": storage}):
        url = _get_attachment_signed_url("blob123")
    # The signed URL reflects the resolved instance's config (not the class default), opts
    # querystring_auth in, and is scoped to GET; the original instance is left untouched.
    assert url == "https://signed/configured/prefix/blob123?auth=True&method=GET"
    assert storage.querystring_auth is False


@pytest.mark.django_db
class TestWorkAreaGroupWriteView(BaseMicroplanningFlagTest):
    """Upsert endpoint: POST with no `id` creates, POST with `id` updates."""

    def url(self, opp_id):
        return reverse("data_export:work_area_group_write", kwargs={"opp_id": opp_id})

    def test_creates_group_when_id_omitted(self, v2_write_client, opportunity):
        response = v2_write_client.post(
            self.url(opportunity.id),
            data={"name": "new-group", "ward": "ward-a", "centroid": "36.8219 -1.2921"},
        )
        assert response.status_code == 201
        group = WorkAreaGroup.objects.get(opportunity=opportunity, name="new-group")
        assert group.ward == "ward-a"
        assert group.centroid.wkt == "POINT (36.8219 -1.2921)"

    def test_updates_name_ward_and_centroid_when_id_present(self, v2_write_client, opportunity):
        group = WorkAreaGroupFactory(opportunity=opportunity, name="old-name")
        response = v2_write_client.post(
            self.url(opportunity.id),
            data={
                "id": group.id,
                "name": "new-name",
                "ward": "new-ward",
                "centroid": "36.8219 -1.2921",
            },
        )
        assert response.status_code == 200
        group.refresh_from_db()
        assert group.name == "new-name"
        assert group.ward == "new-ward"
        assert group.centroid.wkt == "POINT (36.8219 -1.2921)"

    def test_duplicate_name_returns_400(self, v2_write_client, opportunity):
        WorkAreaGroupFactory(opportunity=opportunity, name="taken")
        group = WorkAreaGroupFactory(opportunity=opportunity, name="mine")
        response = v2_write_client.post(self.url(opportunity.id), data={"id": group.id, "name": "taken"})
        assert response.status_code == 400

    def test_same_name_on_same_instance_is_allowed(self, v2_write_client, opportunity):
        group = WorkAreaGroupFactory(opportunity=opportunity, name="mine")
        response = v2_write_client.post(
            self.url(opportunity.id), data={"id": group.id, "name": "mine", "ward": "new-ward"}
        )
        assert response.status_code == 200

    def test_rejects_invalid_lon_lat_centroid(self, v2_write_client, opportunity):
        group = WorkAreaGroupFactory(opportunity=opportunity)
        response = v2_write_client.post(self.url(opportunity.id), data={"id": group.id, "centroid": "not-a-point"})
        assert response.status_code == 400

    def test_unknown_id_returns_404(self, v2_write_client, opportunity):
        response = v2_write_client.post(self.url(opportunity.id), data={"id": 999999, "name": "new-name"})
        assert response.status_code == 404

    def test_requires_write_scope(self, api_client, opportunity, user):
        _add_export_only_credentials(api_client, user)
        _add_v2_header(api_client)
        response = api_client.post(self.url(opportunity.id), data={"name": "new-name", "ward": "ward-a"})
        assert response.status_code == 403

    def test_returns_404_for_unauthorized_opportunity(self, api_client, opportunity, user):
        _add_export_credentials(api_client, user)
        _add_v2_header(api_client)
        response = api_client.post(self.url(opportunity.id), data={"name": "new-name", "ward": "ward-a"})
        assert response.status_code == 404

    def test_member_role_returns_404(self, v2_export_client, opportunity):
        """Mirrors org_admin_required on the equivalent htmx flows: plain membership isn't enough."""
        response = v2_export_client.post(self.url(opportunity.id), data={"name": "new-name", "ward": "ward-a"})
        assert response.status_code == 404


@pytest.mark.django_db(transaction=True)
class TestWorkAreaBulkUpdateView(BaseMicroplanningFlagTest):
    @pytest.fixture(autouse=True)
    def setup_flag_for_managed_opportunity(self, managed_opportunity):
        flag, _ = Flag.objects.get_or_create(name=MICROPLANNING)
        flag.opportunities.add(managed_opportunity)
        flag.flush()

    def url(self, opp_id):
        return reverse("data_export:work_area_bulk_update", kwargs={"opp_id": opp_id})

    def test_updates_multiple_fields_independently_across_items(self, v2_write_client, opportunity):
        area_a = WorkAreaFactory(opportunity=opportunity, expected_visit_count=5, target_population=10)
        area_b = WorkAreaFactory(opportunity=opportunity, expected_visit_count=7, target_population=20)

        payload = [
            {"id": area_a.id, "expected_visit_count": 50},
            {"id": area_b.id, "target_population": 200},
        ]
        response = _patch_json(v2_write_client, self.url(opportunity.id), payload)
        assert response.status_code == 200

        area_a.refresh_from_db()
        area_b.refresh_from_db()
        assert area_a.expected_visit_count == 50
        assert area_a.target_population == 10  # untouched by area_b's update in the same bulk_update call
        assert area_b.target_population == 200
        assert area_b.expected_visit_count == 7  # untouched by area_a's update in the same bulk_update call

    def test_moving_between_groups_recomputes_both_centroids(self, v2_write_client, opportunity):
        old_group = WorkAreaGroupFactory(opportunity=opportunity)
        new_group = WorkAreaGroupFactory(opportunity=opportunity)
        area = WorkAreaFactory(opportunity=opportunity, work_area_group=old_group)

        payload = [{"id": area.id, "work_area_group": new_group.id}]
        response = _patch_json(v2_write_client, self.url(opportunity.id), payload)
        assert response.status_code == 200

        old_group.refresh_from_db()
        new_group.refresh_from_db()
        assert old_group.centroid is None  # no work areas left in the old group
        assert new_group.centroid is not None

    def test_boundary_edit_recomputes_group_centroid_without_group_change(self, v2_write_client, opportunity):
        group = WorkAreaGroupFactory(opportunity=opportunity)
        area = WorkAreaFactory(opportunity=opportunity, work_area_group=group)
        group.update_centroid()
        old_centroid = group.centroid.wkt

        payload = [{"id": area.id, "boundary": "POLYGON((50 10, 51 10, 51 11, 50 11, 50 10))"}]
        response = _patch_json(v2_write_client, self.url(opportunity.id), payload)
        assert response.status_code == 200

        group.refresh_from_db()
        assert group.centroid.wkt != old_centroid

    @pytest.mark.parametrize(
        "overrides",
        [{"centroid": "not-a-point"}, {"boundary": "not-wkt"}],
        ids=["invalid_centroid", "invalid_boundary"],
    )
    def test_rejects_invalid_field_values(self, v2_write_client, opportunity, overrides):
        area = WorkAreaFactory(opportunity=opportunity)
        payload = [{"id": area.id, **overrides}]
        response = _patch_json(v2_write_client, self.url(opportunity.id), payload)
        assert response.status_code == 400
        assert list(overrides.keys())[0] in response.json()[0]

    @pytest.mark.parametrize("field_name", ["work_area_group", "opportunity_access"])
    def test_rejects_foreign_key_from_other_opportunity(self, v2_write_client, opportunity, field_name):
        other_opportunity = OpportunityFactory()
        area = WorkAreaFactory(opportunity=opportunity)
        foreign_id = (
            WorkAreaGroupFactory(opportunity=other_opportunity).id
            if field_name == "work_area_group"
            else OpportunityAccessFactory(opportunity=other_opportunity).id
        )
        payload = [{"id": area.id, field_name: foreign_id}]
        response = _patch_json(v2_write_client, self.url(opportunity.id), payload)
        assert response.status_code == 400
        assert field_name in response.json()[0]

    def test_rejects_work_area_id_from_other_opportunity(self, v2_write_client, opportunity):
        other_area = WorkAreaFactory(opportunity=OpportunityFactory(), target_population=1)
        payload = [{"id": other_area.id, "target_population": 5}]
        response = _patch_json(v2_write_client, self.url(opportunity.id), payload)
        assert response.status_code == 400
        other_area.refresh_from_db()
        assert other_area.target_population == 1

    def test_org_admin_without_pm_role_cannot_assign(self, v2_write_client, opportunity):
        access = OpportunityAccessFactory(opportunity=opportunity)
        area = WorkAreaFactory(opportunity=opportunity, status=WorkAreaStatus.UNASSIGNED)
        payload = [{"id": area.id, "opportunity_access": access.id}]
        response = _patch_json(v2_write_client, self.url(opportunity.id), payload)
        assert response.status_code == 404
        area.refresh_from_db()
        assert area.opportunity_access_id is None

    def test_org_admin_without_pm_role_can_edit_other_fields(self, v2_write_client, opportunity):
        area = WorkAreaFactory(opportunity=opportunity, target_population=1)
        payload = [{"id": area.id, "target_population": 99}]
        response = _patch_json(v2_write_client, self.url(opportunity.id), payload)
        assert response.status_code == 200
        area.refresh_from_db()
        assert area.target_population == 99

    @mock.patch("commcare_connect.microplanning.helpers.bulk_create_or_update_cases_by_work_areas")
    def test_pm_admin_assign_flips_status_syncs_hq_and_notifies(
        self, mock_hq_sync, api_client, managed_opportunity, program_manager_org_user_admin
    ):
        _add_export_credentials(api_client, program_manager_org_user_admin)
        _add_v2_header(api_client)
        access = OpportunityAccessFactory(opportunity=managed_opportunity)
        area = WorkAreaFactory(opportunity=managed_opportunity, status=WorkAreaStatus.UNASSIGNED)

        payload = [{"id": area.id, "opportunity_access": access.id}]
        with mock.patch(
            "commcare_connect.microplanning.helpers.send_work_area_assignment_notification.delay"
        ) as mock_notify:
            response = _patch_json(api_client, self.url(managed_opportunity.id), payload)

        assert response.status_code == 200
        area.refresh_from_db()
        assert area.opportunity_access_id == access.id
        assert area.status == WorkAreaStatus.NOT_VISITED
        mock_hq_sync.assert_called_once()
        mock_notify.assert_called_once_with(access.id)

    @mock.patch("commcare_connect.microplanning.helpers.bulk_create_or_update_cases_by_work_areas")
    def test_hq_failure_during_assignment_rolls_back(
        self, mock_hq_sync, api_client, managed_opportunity, program_manager_org_user_admin
    ):
        mock_hq_sync.side_effect = CommCareHQAPIException("HQ unavailable")
        _add_export_credentials(api_client, program_manager_org_user_admin)
        _add_v2_header(api_client)
        access = OpportunityAccessFactory(opportunity=managed_opportunity)
        area = WorkAreaFactory(opportunity=managed_opportunity, status=WorkAreaStatus.UNASSIGNED)

        payload = [{"id": area.id, "opportunity_access": access.id}]
        response = _patch_json(api_client, self.url(managed_opportunity.id), payload)

        assert response.status_code == 502
        area.refresh_from_db()
        assert area.opportunity_access_id is None
        assert area.status == WorkAreaStatus.UNASSIGNED

    @mock.patch("commcare_connect.microplanning.helpers.bulk_create_or_update_cases")
    def test_explicit_null_opportunity_access_unassigns(
        self, mock_hq_unassign, api_client, managed_opportunity, program_manager_org_user_admin
    ):
        _add_export_credentials(api_client, program_manager_org_user_admin)
        _add_v2_header(api_client)
        access = OpportunityAccessFactory(opportunity=managed_opportunity)
        area = WorkAreaFactory(
            opportunity=managed_opportunity, opportunity_access=access, status=WorkAreaStatus.NOT_VISITED
        )

        payload = [{"id": area.id, "opportunity_access": None}]
        response = _patch_json(api_client, self.url(managed_opportunity.id), payload)

        assert response.status_code == 200
        body = response.json()
        assert body["unassign_skipped"] == 0
        assert body["unassign_failed_ids"] == []
        area.refresh_from_db()
        assert area.opportunity_access_id is None
        assert area.status == WorkAreaStatus.UNASSIGNED

    def test_explicit_null_opportunity_access_skips_ineligible_work_area(
        self, api_client, managed_opportunity, program_manager_org_user_admin
    ):
        _add_export_credentials(api_client, program_manager_org_user_admin)
        _add_v2_header(api_client)
        access = OpportunityAccessFactory(opportunity=managed_opportunity)
        area = WorkAreaFactory(
            opportunity=managed_opportunity, opportunity_access=access, status=WorkAreaStatus.VISITED
        )

        payload = [{"id": area.id, "opportunity_access": None}]
        response = _patch_json(api_client, self.url(managed_opportunity.id), payload)

        assert response.status_code == 200
        assert response.json()["unassign_skipped"] == 1
        area.refresh_from_db()
        assert area.opportunity_access_id == access.id
        assert area.status == WorkAreaStatus.VISITED


def test_work_area_bulk_create_row_from_item_keys_match_get_fieldnames():
    """Ensures row_from_item() stays in sync with get_fieldnames(). If a field is
    added, renamed, or removed in the importer headers but not updated in
    row_from_item(), csv.DictWriter silently writes an empty value instead of
    raising an error.This test fails loudly the moment the two drift apart, instead of a mystery blank column."""
    view = WorkAreaBulkCreateView()
    sample_item = {
        "slug": "s",
        "ward": "w",
        "centroid": "c",
        "boundary": "b",
        "building_count": 1,
        "expected_visit_count": 1,
        "target_population": 1,
        "lga": "l",
        "state": "st",
        "work_area_group_name": "g",
        "implementation_area_name": "i",
    }
    assert set(view.row_from_item(sample_item)) == set(view.get_fieldnames())


@pytest.mark.django_db(transaction=True)
class TestWorkAreaBulkCreateView(BaseMicroplanningFlagTest):
    def url(self, opp_id):
        return reverse("data_export:work_area_bulk_create", kwargs={"opp_id": opp_id})

    def _item(self, **overrides):
        item = {
            "slug": "area-1",
            "ward": "ward-a",
            "centroid": "36.8 -1.29",
            "boundary": "POLYGON((36.8 -1.29, 36.82 -1.29, 36.82 -1.30, 36.8 -1.29))",
            "building_count": 5,
            "expected_visit_count": 10,
            "target_population": 100,
            "lga": "lga-1",
            "state": "state-1",
        }
        item.update(overrides)
        return item

    def test_creates_work_areas(self, v2_write_client, opportunity):
        payload = [self._item(slug="area-1"), self._item(slug="area-2")]
        response = _post_json(v2_write_client, self.url(opportunity.id), payload)
        assert response.status_code == 201
        assert response.json() == {"created": 2}

        area = WorkArea.objects.get(opportunity=opportunity, slug="area-1")
        assert area.ward == "ward-a"
        assert area.centroid.wkt == "POINT (36.8 -1.29)"
        assert area.building_count == 5
        assert area.expected_visit_count == 10
        assert area.target_population == 100
        assert area.case_properties == {"lga": "lga-1", "state": "state-1"}

    def test_resolves_existing_work_area_group_by_name(self, v2_write_client, opportunity):
        group = WorkAreaGroupFactory(opportunity=opportunity, name="group-a", ward="original-ward")
        payload = [self._item(work_area_group_name="group-a")]
        response = _post_json(v2_write_client, self.url(opportunity.id), payload)
        assert response.status_code == 201

        area = WorkArea.objects.get(opportunity=opportunity, slug="area-1")
        assert area.work_area_group_id == group.id
        assert WorkAreaGroup.objects.filter(opportunity=opportunity, name="group-a").count() == 1

    def test_creates_missing_work_area_group_by_name(self, v2_write_client, opportunity):
        payload = [self._item(work_area_group_name="new-group", ward="ward-b")]
        response = _post_json(v2_write_client, self.url(opportunity.id), payload)
        assert response.status_code == 201

        group = WorkAreaGroup.objects.get(opportunity=opportunity, name="new-group")
        assert group.ward == "ward-b"
        area = WorkArea.objects.get(opportunity=opportunity, slug="area-1")
        assert area.work_area_group_id == group.id

    def test_partial_group_names_rejected(self, v2_write_client, opportunity):
        payload = [self._item(slug="area-1", work_area_group_name="group-a"), self._item(slug="area-2")]
        response = _post_json(v2_write_client, self.url(opportunity.id), payload)
        assert response.status_code == 400
        assert "required for all Work Areas" in response.json()["errors"][0]
        assert not WorkArea.objects.filter(opportunity=opportunity).exists()

    def test_duplicate_slug_in_payload_rejected(self, v2_write_client, opportunity):
        payload = [self._item(slug="dup"), self._item(slug="dup")]
        response = _post_json(v2_write_client, self.url(opportunity.id), payload)
        assert response.status_code == 400
        assert "duplicate" in " ".join(response.json()["errors"]).lower()
        assert not WorkArea.objects.filter(opportunity=opportunity).exists()

    def test_existing_slug_in_db_rejected(self, v2_write_client, opportunity):
        WorkAreaFactory(opportunity=opportunity, slug="taken")
        payload = [self._item(slug="taken")]
        response = _post_json(v2_write_client, self.url(opportunity.id), payload)
        assert response.status_code == 400
        assert "already exists" in " ".join(response.json()["errors"]).lower()

    def test_invalid_centroid_format_rejected(self, v2_write_client, opportunity):
        payload = [self._item(centroid="not-a-point")]
        response = _post_json(v2_write_client, self.url(opportunity.id), payload)
        assert response.status_code == 400
        assert "lon lat" in " ".join(response.json()["errors"]).lower()
        assert not WorkArea.objects.filter(opportunity=opportunity).exists()

    def test_links_to_existing_implementation_area_by_name(self, v2_write_client, opportunity):
        implementation_area = ImplementationAreaFactory(opportunity=opportunity, name="zone-a")
        payload = [self._item(implementation_area_name="zone-a")]
        response = _post_json(v2_write_client, self.url(opportunity.id), payload)
        assert response.status_code == 201

        area = WorkArea.objects.get(opportunity=opportunity, slug="area-1")
        assert area.implementation_area_id == implementation_area.id

    def test_stores_implementation_area_name_when_not_yet_created(self, v2_write_client, opportunity):
        payload = [self._item(implementation_area_name="not-created-yet")]
        response = _post_json(v2_write_client, self.url(opportunity.id), payload)
        assert response.status_code == 201

        area = WorkArea.objects.get(opportunity=opportunity, slug="area-1")
        assert area.implementation_area_id is None
        assert area.implementation_area_name == "not-created-yet"
