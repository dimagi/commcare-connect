import datetime

import pytest
from django.contrib.auth.models import Permission
from django.urls import reverse
from django.utils.timezone import now

from commcare_connect.microplanning.tests.factories import WorkAreaFactory, WorkAreaGroupFactory
from commcare_connect.opportunity.tests.factories import AssignedTaskFactory, OpportunityAccessFactory, TaskTypeFactory
from commcare_connect.users.tests.factories import LLOEntityFactory


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
        opp_access = OpportunityAccessFactory(opportunity=opportunity)
        group = WorkAreaGroupFactory(opportunity=opportunity, opportunity_access=opp_access)
        url = reverse("data_export:work_area_group_data", kwargs={"opp_id": opportunity.id})
        response = v2_export_client.get(url)
        assert response.status_code == 200
        data = response.json()
        assert len(data["results"]) == 1
        result = data["results"][0]
        assert result["id"] == group.id
        assert result["name"] == group.name
        assert result["assigned_username"] == opp_access.user.username

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
