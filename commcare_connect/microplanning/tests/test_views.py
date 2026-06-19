from __future__ import annotations

import csv as csv_mod
import io
import json
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock
from unittest.mock import MagicMock, patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.utils import OperationalError
from django.test import Client
from django.urls import reverse

from commcare_connect.flags.flag_names import MICROPLANNING
from commcare_connect.flags.models import Flag
from commcare_connect.microplanning import views as microplanning_views
from commcare_connect.microplanning.filters import WorkAreaMapFilterSet
from commcare_connect.microplanning.models import WorkArea, WorkAreaStatus
from commcare_connect.microplanning.tasks import WorkAreaCSVExporter
from commcare_connect.microplanning.tests.factories import (
    WorkAreaFactory,
    WorkAreaGroupFactory,
    WorkAreaInaccessibilityRequestFactory,
)
from commcare_connect.microplanning.views import UserVisitVectorLayer, get_metrics_for_microplanning
from commcare_connect.opportunity.models import BlobMeta, VisitValidationStatus
from commcare_connect.opportunity.tests.factories import OpportunityAccessFactory, OpportunityFactory, UserVisitFactory
from commcare_connect.utils.commcarehq_api import CommCareHQAPIException


class BaseMicroplanningFlagTest:
    @pytest.fixture(autouse=True)
    def setup_microplanning_flag(self, opportunity, request):
        enabled = getattr(request, "param", True)
        if not enabled:
            return

        flag, _ = Flag.objects.get_or_create(name=MICROPLANNING)
        flag.opportunities.add(opportunity)
        flag.flush()


@pytest.mark.django_db
class TestWorkAreaUpload(BaseMicroplanningFlagTest):
    # --- Common CSV for all tests ---
    CSV_CONTENT = (
        b"Area Slug,Ward,Centroid,Boundary,Building Count,Expected Visit Count\n"
        b"area-1,Ward1,77.1 28.6,POLYGON((77 28,78 28,78 29,77 29,77 28)),5,6\n"
    )

    @pytest.fixture
    def csv_file(self):
        return SimpleUploadedFile("test.csv", self.CSV_CONTENT, content_type="text/csv")

    def get_url(self, org_slug, opp_id):
        return reverse(
            "microplanning:upload_work_areas",
            kwargs={"org_slug": org_slug, "opp_id": opp_id},
        )

    @patch("commcare_connect.microplanning.views.import_work_areas_task.delay")
    def test_locking_mechanism(self, mock_delay, client, org_user_admin, opportunity, csv_file):
        url = self.get_url(opportunity.organization.slug, opportunity.opportunity_id)
        client.force_login(org_user_admin)

        # Mock celery task
        mock_task = MagicMock()
        mock_task.id = "task-123"
        mock_delay.return_value = mock_task

        # First upload triggers the task
        response1 = client.post(url, {"csv_file": csv_file})
        assert response1.status_code == 302
        messages = list(response1.wsgi_request._messages)
        assert "Work Area upload has been started." in str(messages[0])
        assert "task_id=task-123" in response1.url
        assert mock_delay.call_count == 1

        # Second upload while first is "in progress" is blocked
        response2 = client.post(url, {"csv_file": csv_file})
        assert response2.status_code == 302
        messages = list(response2.wsgi_request._messages)
        assert "An import for this opportunity is already in progress." in str(messages[1])
        assert mock_delay.call_count == 1  # No new task

    @pytest.mark.parametrize("setup_microplanning_flag", [False], indirect=True)
    @patch("commcare_connect.microplanning.views.import_work_areas_task.delay")
    def test_flagged_permission_required(self, mock_delay, client, org_user_admin, opportunity, csv_file):
        """
        Ensure upload is only allowed if the opportunity is flagged for microplanning.
        """
        url = self.get_url(opportunity.organization.slug, opportunity.opportunity_id)
        client.force_login(org_user_admin)

        response = client.post(url, {"csv_file": csv_file})
        assert response.status_code == 404
        assert mock_delay.call_count == 0


@pytest.mark.django_db
class TestMicroplanningHomeView(BaseMicroplanningFlagTest):
    def url(self, org_slug: str, opp_id: str):
        return reverse("microplanning:microplanning_home", args=(org_slug, opp_id))

    def test_success(self, client: Client, settings, organization, org_user_admin, opportunity):
        settings.MAPBOX_TOKEN = "test-mapbox-token"
        client.force_login(org_user_admin)
        response = client.get(self.url(organization.slug, str(opportunity.opportunity_id)))

        assert response.status_code == 200
        assert any(t.name == "microplanning/home.html" for t in response.templates)

    @pytest.mark.parametrize("setup_microplanning_flag", [False], indirect=True)
    def test_flag_disabled(self, client: Client, organization, org_user_admin, opportunity):
        client.force_login(org_user_admin)
        response = client.get(self.url(organization.slug, str(opportunity.opportunity_id)))
        assert response.status_code == 404

    def test_unauthenticated(self, client: Client, organization, org_user_member, opportunity):
        client.force_login(org_user_member)
        response = client.get(self.url(organization.slug, str(opportunity.opportunity_id)))
        assert response.status_code == 404


@pytest.mark.django_db
class TestModifyWorkAreaUpdateView(BaseMicroplanningFlagTest):
    template_name = "microplanning/work_area_form.html"

    def url(self, org_slug, opp_id, work_area_id):
        return reverse("microplanning:modify_work_area", args=(org_slug, opp_id, work_area_id))

    def test_404_wrong_opportunity_work_area(self, client, org_user_admin, opportunity):
        other_opportunity = OpportunityFactory()
        work_area = WorkAreaFactory(opportunity=opportunity)
        client.force_login(org_user_admin)
        response = client.get(
            self.url(other_opportunity.organization.slug, str(opportunity.opportunity_id), work_area.id)
        )
        assert response.status_code == 404

    def test_get_renders_form_with_work_area_data(self, client, org_user_admin, opportunity):
        group = WorkAreaGroupFactory(opportunity=opportunity)
        work_area = WorkAreaFactory(opportunity=opportunity, expected_visit_count=15, work_area_group=group)
        client.force_login(org_user_admin)
        response = client.get(self.url(opportunity.organization.slug, str(opportunity.opportunity_id), work_area.id))

        assert response.status_code == 200
        assert any(t.name == self.template_name for t in response.templates)
        assert response.context["work_area"] == work_area

    @patch("commcare_connect.microplanning.views.create_or_update_case_by_work_area")
    def test_successful_field_updates(self, mock_sync, client, org_user_admin, opportunity):
        group = WorkAreaGroupFactory(opportunity=opportunity)
        access = OpportunityAccessFactory(opportunity=opportunity)
        work_area = WorkAreaFactory(opportunity=opportunity, expected_visit_count=10, opportunity_access=access)

        initial_event_count = (
            work_area.expected_visit_count_work_area_group_status_opportunity_access_excluded_reason_events.count()
        )
        assert work_area.work_area_group is None
        new_expected_visit_count = 25
        client.force_login(org_user_admin)
        response = client.post(
            self.url(opportunity.organization.slug, str(opportunity.opportunity_id), work_area.id),
            {
                "expected_visit_count": new_expected_visit_count,
                "work_area_group": group.id,
                "reason": "Boundary adjusted",
            },
        )
        assert response.status_code == 204
        trigger = json.loads(response["HX-Trigger"])
        assert "workAreaUpdated" in trigger
        assert trigger["workAreaUpdated"]["expected_visit_count"] == new_expected_visit_count
        assert trigger["workAreaUpdated"]["group_id"] == group.id
        assert trigger["workAreaUpdated"]["group_name"] == group.name
        assert mock_sync.call_count == 1

        work_area.refresh_from_db()
        assert work_area.expected_visit_count == new_expected_visit_count
        assert work_area.work_area_group == group

        events = work_area.expected_visit_count_work_area_group_status_opportunity_access_excluded_reason_events
        assert events.count() == initial_event_count + 1
        event = events.last()
        assert event.pgh_context.metadata["reason"] == "Boundary adjusted"
        assert event.expected_visit_count == new_expected_visit_count
        assert event.work_area_group == group

    @patch("commcare_connect.microplanning.views.create_or_update_case_by_work_area")
    def test_no_history_created_when_nothing_changes(self, mock_sync, client, org_user_admin, opportunity):
        group = WorkAreaGroupFactory(opportunity=opportunity)
        work_area = WorkAreaFactory(opportunity=opportunity, expected_visit_count=10, work_area_group=group)
        initial_event_count = (
            work_area.expected_visit_count_work_area_group_status_opportunity_access_excluded_reason_events.count()
        )

        client.force_login(org_user_admin)
        response = client.post(
            self.url(opportunity.organization.slug, str(opportunity.opportunity_id), work_area.id),
            {
                "expected_visit_count": 10,
                "work_area_group": group.id,
                "reason": "No change",
            },
        )

        work_area.refresh_from_db()
        assert response.status_code == 204
        assert (
            work_area.expected_visit_count_work_area_group_status_opportunity_access_excluded_reason_events.count()
            == initial_event_count
        )
        assert mock_sync.call_count == 0  # No sync since nothing changed
        assert work_area.work_area_group == group
        assert work_area.expected_visit_count == 10

    def test_invalid_form_returns_errors(self, client, org_user_admin, opportunity):
        work_area = WorkAreaFactory(opportunity=opportunity, expected_visit_count=10)

        client.force_login(org_user_admin)
        response = client.post(
            self.url(opportunity.organization.slug, str(opportunity.opportunity_id), work_area.id),
            {"expected_visit_count": "not-a-number"},
        )

        assert response.status_code == 200
        assert any(t.name == self.template_name for t in response.templates)
        assert response.context["form"].errors
        work_area.refresh_from_db()
        assert work_area.expected_visit_count == 10  # unchanged

    @patch("commcare_connect.microplanning.views.create_or_update_case_by_work_area")
    def test_hq_sync_failure_returns_form_error(self, mock_sync, client, org_user_admin, opportunity):
        group = WorkAreaGroupFactory(opportunity=opportunity)
        access = OpportunityAccessFactory(opportunity=opportunity)
        work_area = WorkAreaFactory(
            opportunity=opportunity, expected_visit_count=10, work_area_group=group, opportunity_access=access
        )
        mock_sync.side_effect = CommCareHQAPIException("sync failed")

        client.force_login(org_user_admin)
        response = client.post(
            self.url(opportunity.organization.slug, str(opportunity.opportunity_id), work_area.id),
            {
                "expected_visit_count": 25,
                "work_area_group": group.id,
                "reason": "Test",
            },
        )

        assert response.status_code == 200
        assert mock_sync.call_count == 1
        assert any(t.name == self.template_name for t in response.templates)
        assert response.context["form"].non_field_errors()
        work_area.refresh_from_db()
        assert work_area.expected_visit_count == 10  # rolled back due to atomic transaction

    @pytest.mark.parametrize(
        "initial_status,prior_visits,old_count,new_count,expected_status",
        [
            # decreased below visit count → EXPECTED_VISIT_REACHED
            (WorkAreaStatus.VISITED, 3, 5, 2, WorkAreaStatus.EXPECTED_VISIT_REACHED),
            # no visits → status unchanged regardless of count change
            (WorkAreaStatus.NOT_VISITED, 0, 5, 2, WorkAreaStatus.NOT_VISITED),
            # only group changed, not expected_visit_count → status unchanged
            (WorkAreaStatus.VISITED, 3, 5, 5, WorkAreaStatus.VISITED),
        ],
    )
    @patch("commcare_connect.microplanning.views.create_or_update_case_by_work_area")
    def test_expected_visit_count_change_reevaluates_status(
        self,
        mock_sync,
        client,
        org_user_admin,
        opportunity,
        initial_status,
        prior_visits,
        old_count,
        new_count,
        expected_status,
    ):
        access = OpportunityAccessFactory(opportunity=opportunity)
        work_area = WorkAreaFactory(
            opportunity=opportunity,
            opportunity_access=access,
            status=initial_status,
            expected_visit_count=old_count,
        )
        for _ in range(prior_visits):
            UserVisitFactory(
                opportunity_access=access,
                work_area=work_area,
                opportunity=opportunity,
                status=VisitValidationStatus.approved,
            )

        client.force_login(org_user_admin)
        client.post(
            self.url(opportunity.organization.slug, str(opportunity.opportunity_id), work_area.id),
            {"expected_visit_count": new_count},
        )

        work_area.refresh_from_db()
        assert work_area.status == expected_status


@pytest.mark.django_db
class TestWorkAreaTileViewFiltering(BaseMicroplanningFlagTest):
    TILE_Z, TILE_X, TILE_Y = 10, 732, 427

    def tile_url(self, org_slug, opp_id):
        return reverse(
            "microplanning:workareas_tiles",
            kwargs={"org_slug": org_slug, "opp_id": opp_id, "z": self.TILE_Z, "x": self.TILE_X, "y": self.TILE_Y},
        )

    def _get_tile_queryset(self, client, org_user_admin, opportunity, query_params=None):
        client.force_login(org_user_admin)
        url = self.tile_url(opportunity.organization.slug, str(opportunity.opportunity_id))
        original_get_queryset = microplanning_views.WorkAreaVectorLayer.get_queryset
        captured_qs = []

        def capturing_get_queryset(self_layer):
            qs = original_get_queryset(self_layer)
            captured_qs.append(qs)
            return qs

        # the actual MVT (vector tile) response is binary protobuf and hard to assert
        with patch.object(microplanning_views.WorkAreaVectorLayer, "get_queryset", capturing_get_queryset):
            response = client.get(url, data=query_params or {})

        assert response.status_code in (200, 204)
        assert len(captured_qs) == 1
        return captured_qs[0]

    def test_unfiltered_returns_all_work_areas(self, client, org_user_admin, opportunity):
        WorkAreaFactory(opportunity=opportunity, status=WorkAreaStatus.VISITED)
        WorkAreaFactory(opportunity=opportunity, status=WorkAreaStatus.NOT_VISITED)
        qs = self._get_tile_queryset(client, org_user_admin, opportunity)
        assert qs.count() == 2

    def test_status_filter_forwarded(self, client, org_user_admin, opportunity):
        WorkAreaFactory(opportunity=opportunity, status=WorkAreaStatus.VISITED)
        wa_not_visited = WorkAreaFactory(opportunity=opportunity, status=WorkAreaStatus.NOT_VISITED)
        qs = self._get_tile_queryset(
            client,
            org_user_admin,
            opportunity,
            query_params={"status": WorkAreaStatus.NOT_VISITED},
        )
        assert list(qs.values_list("id", flat=True)) == [wa_not_visited.id]

    def test_assignee_filter_forwarded(self, client, org_user_admin, opportunity):
        access = OpportunityAccessFactory(opportunity=opportunity)
        wa_assigned = WorkAreaFactory(
            opportunity=opportunity, opportunity_access=access, status=WorkAreaStatus.NOT_VISITED
        )
        WorkAreaFactory(opportunity=opportunity, status=WorkAreaStatus.UNASSIGNED)

        qs = self._get_tile_queryset(
            client,
            org_user_admin,
            opportunity,
            query_params={"assignee": access.user.pk},
        )
        assert set(qs.values_list("id", flat=True)) == {wa_assigned.id}

    def test_excludes_other_opportunity(self, client, org_user_admin, opportunity):
        other_opp = OpportunityFactory()
        WorkAreaFactory(opportunity=opportunity)
        WorkAreaFactory(opportunity=other_opp)
        qs = self._get_tile_queryset(client, org_user_admin, opportunity)
        assert qs.count() == 1

    def test_annotations_present(self, client, org_user_admin, opportunity):
        access = OpportunityAccessFactory(opportunity=opportunity)
        group = WorkAreaGroupFactory(opportunity=opportunity)
        WorkAreaFactory(opportunity=opportunity, work_area_group=group, opportunity_access=access)

        qs = self._get_tile_queryset(client, org_user_admin, opportunity)
        row = qs.first()
        assert row.group_id == group.id
        assert row.group_name == group.name
        assert row.assignee_name == access.user.name


@pytest.mark.django_db
class TestWorkAreaMapFilterSet:
    @pytest.fixture
    def work_areas(self, opportunity):
        access = OpportunityAccessFactory(opportunity=opportunity)
        group = WorkAreaGroupFactory(opportunity=opportunity)

        wa_not_visited = WorkAreaFactory(
            opportunity=opportunity,
            work_area_group=group,
            opportunity_access=access,
            status=WorkAreaStatus.NOT_VISITED,
        )
        wa_visited = WorkAreaFactory(
            opportunity=opportunity, work_area_group=group, opportunity_access=access, status=WorkAreaStatus.VISITED
        )
        wa_unassigned = WorkAreaFactory(opportunity=opportunity, status=WorkAreaStatus.UNASSIGNED)
        return SimpleNamespace(
            access=access,
            group=group,
            wa_not_visited=wa_not_visited,
            wa_visited=wa_visited,
            wa_unassigned=wa_unassigned,
        )

    def _filter_ids(self, params, opportunity):
        qs = WorkArea.objects.filter(opportunity=opportunity)
        return set(WorkAreaMapFilterSet(params, queryset=qs, opportunity=opportunity).qs.values_list("id", flat=True))

    @pytest.mark.parametrize(
        "statuses, expected_attrs",
        [
            ([WorkAreaStatus.VISITED], ["wa_visited"]),
            ([WorkAreaStatus.NOT_VISITED, WorkAreaStatus.UNASSIGNED], ["wa_not_visited", "wa_unassigned"]),
        ],
        ids=["single_status", "multiple_statuses"],
    )
    def test_status_filter(self, opportunity, work_areas, statuses, expected_attrs):
        expected = {getattr(work_areas, attr).id for attr in expected_attrs}
        assert self._filter_ids({"status": statuses}, opportunity) == expected

    def test_assignee_filter_excludes_unassigned(self, opportunity, work_areas):
        result = self._filter_ids({"assignee": [work_areas.access.user.pk]}, opportunity)
        assert result == {work_areas.wa_not_visited.id, work_areas.wa_visited.id}

    @pytest.mark.parametrize(
        "params, expected_attrs",
        [
            ({"start_date": "2026-03-15"}, ["wa_not_visited"]),
            ({"end_date": "2026-03-15"}, ["wa_visited"]),
            ({"start_date": "2026-03-15", "end_date": "2026-03-22"}, ["wa_not_visited"]),
        ],
        ids=["start_date_gte", "end_date_lte", "date_range"],
    )
    def test_date_filters(self, opportunity, work_areas, params, expected_attrs):
        for wa_attr, visit_date in [("wa_visited", "2026-03-10"), ("wa_not_visited", "2026-03-20")]:
            UserVisitFactory(
                opportunity=opportunity,
                user=work_areas.access.user,
                work_area=getattr(work_areas, wa_attr),
                visit_date=datetime.fromisoformat(f"{visit_date}T00:00:00+00:00"),
            )
        expected = {getattr(work_areas, attr).id for attr in expected_attrs}
        assert self._filter_ids(params, opportunity) == expected

    def test_date_filter_no_duplicates(self, opportunity, work_areas):
        """A work area with multiple visits in the range should appear only once."""
        for day in ("2026-03-10", "2026-03-12", "2026-03-14"):
            UserVisitFactory(
                opportunity=opportunity,
                user=work_areas.access.user,
                work_area=work_areas.wa_visited,
                visit_date=datetime.fromisoformat(f"{day}T00:00:00+00:00"),
            )
        qs = WorkArea.objects.filter(opportunity=opportunity)
        result = list(
            WorkAreaMapFilterSet(
                {"start_date": "2026-03-11", "end_date": "2026-03-15"},
                queryset=qs,
                opportunity=opportunity,
            ).qs.values_list("id", flat=True)
        )
        assert result == [work_areas.wa_visited.id]

    def test_combined_status_and_assignee(self, opportunity, work_areas):
        result = self._filter_ids(
            {"status": [WorkAreaStatus.NOT_VISITED], "assignee": [work_areas.access.user.pk]}, opportunity
        )
        assert result == {work_areas.wa_not_visited.id}

    def test_assignee_queryset_requires_opportunity(self):
        empty_qs = WorkArea.objects.none()
        fs = WorkAreaMapFilterSet({}, queryset=empty_qs)
        assert fs.filters["assignee"].queryset.count() == 0


@pytest.mark.django_db
class TestUserVisitVectorLayer:
    @pytest.fixture
    def visit_data(self, opportunity):
        access = OpportunityAccessFactory(opportunity=opportunity)
        work_area = WorkAreaFactory(opportunity=opportunity, opportunity_access=access)
        return SimpleNamespace(access=access, work_area=work_area)

    def test_queryset_includes_visits_with_location(self, opportunity, visit_data):
        visit = UserVisitFactory(
            opportunity=opportunity,
            user=visit_data.access.user,
            work_area=visit_data.work_area,
            location="28.6 77.1 0 0",
        )
        layer = UserVisitVectorLayer(opportunity=opportunity)
        qs = layer.get_queryset()
        assert qs.filter(id=visit.id).exists()

    def test_queryset_excludes_visits_without_location(self, opportunity, visit_data):
        UserVisitFactory(
            opportunity=opportunity,
            user=visit_data.access.user,
            work_area=visit_data.work_area,
            location=None,
        )
        UserVisitFactory(
            opportunity=opportunity,
            user=visit_data.access.user,
            work_area=visit_data.work_area,
            location="",
        )
        layer = UserVisitVectorLayer(opportunity=opportunity)
        assert layer.get_queryset().count() == 0

    def test_queryset_annotates_location_point(self, opportunity, visit_data):
        UserVisitFactory(
            opportunity=opportunity,
            user=visit_data.access.user,
            work_area=visit_data.work_area,
            location="28.6 77.1 0 0",
        )
        layer = UserVisitVectorLayer(opportunity=opportunity)
        visit = layer.get_queryset().first()

        assert round(visit["location_point"].x, 1) == 77.1
        assert round(visit["location_point"].y, 1) == 28.6
        assert visit["work_area_id"] == visit_data.work_area.id

    def test_queryset_only_includes_visits_for_opportunity(self, opportunity, visit_data):
        other_opp = OpportunityFactory()
        other_access = OpportunityAccessFactory(opportunity=other_opp)
        UserVisitFactory(
            opportunity=other_opp,
            user=other_access.user,
            location="28.6 77.1 0 0",
        )
        UserVisitFactory(
            opportunity=opportunity,
            user=visit_data.access.user,
            work_area=visit_data.work_area,
            location="28.6 77.1 0 0",
        )
        layer = UserVisitVectorLayer(opportunity=opportunity)
        assert layer.get_queryset().count() == 1

    def test_filter_by_assignee(self, opportunity, visit_data):
        other_access = OpportunityAccessFactory(opportunity=opportunity)
        other_wa = WorkAreaFactory(opportunity=opportunity, opportunity_access=other_access)
        UserVisitFactory(
            opportunity=opportunity,
            user=visit_data.access.user,
            work_area=visit_data.work_area,
            location="28.6 77.1 0 0",
        )
        UserVisitFactory(
            opportunity=opportunity,
            user=other_access.user,
            work_area=other_wa,
            location="28.6 77.1 0 0",
        )
        layer = UserVisitVectorLayer(
            opportunity=opportunity,
            filter_params={"assignee": [visit_data.access.user.pk]},
        )
        assert layer.get_queryset().count() == 1

    def test_filter_by_date_range(self, opportunity, visit_data):
        UserVisitFactory(
            opportunity=opportunity,
            user=visit_data.access.user,
            work_area=visit_data.work_area,
            location="28.6 77.1 0 0",
            visit_date=datetime(2025, 1, 15),
        )
        UserVisitFactory(
            opportunity=opportunity,
            user=visit_data.access.user,
            work_area=visit_data.work_area,
            location="28.6 77.1 0 0",
            visit_date=datetime(2025, 3, 15),
        )
        layer = UserVisitVectorLayer(
            opportunity=opportunity,
            filter_params={"start_date": "2025-01-01", "end_date": "2025-01-31"},
        )
        assert layer.get_queryset().count() == 1

    def test_filter_by_work_area_status(self, opportunity, visit_data):
        visit_data.work_area.status = WorkAreaStatus.VISITED
        visit_data.work_area.save()
        other_access = OpportunityAccessFactory(opportunity=opportunity)
        other_wa = WorkAreaFactory(
            opportunity=opportunity, opportunity_access=other_access, status=WorkAreaStatus.NOT_VISITED
        )
        UserVisitFactory(
            opportunity=opportunity,
            user=visit_data.access.user,
            work_area=visit_data.work_area,
            location="28.6 77.1 0 0",
        )
        UserVisitFactory(
            opportunity=opportunity,
            user=other_access.user,
            work_area=other_wa,
            location="28.6 77.1 0 0",
        )
        layer = UserVisitVectorLayer(
            opportunity=opportunity,
            filter_params={"status": [WorkAreaStatus.VISITED]},
        )
        assert layer.get_queryset().count() == 1

    def test_no_filters_returns_all(self, opportunity, visit_data):
        UserVisitFactory(
            opportunity=opportunity,
            user=visit_data.access.user,
            work_area=visit_data.work_area,
            location="28.6 77.1 0 0",
        )
        UserVisitFactory(
            opportunity=opportunity,
            user=visit_data.access.user,
            work_area=visit_data.work_area,
            location="28.7 77.2 0 0",
        )
        layer = UserVisitVectorLayer(opportunity=opportunity, filter_params={})
        assert layer.get_queryset().count() == 2


@pytest.mark.django_db
class TestDownloadWorkAreas(BaseMicroplanningFlagTest):
    def url(self, opportunity):
        return reverse(
            "microplanning:download_work_areas",
            kwargs={"org_slug": opportunity.organization.slug, "opp_id": opportunity.opportunity_id},
        )

    def _parse_csv(self, response):
        content = b"".join(response.streaming_content).decode("utf-8")
        return list(csv_mod.reader(io.StringIO(content)))

    def test_streams_csv_with_correct_headers_and_data(self, client, org_user_admin, opportunity):
        wa = WorkAreaFactory(
            opportunity=opportunity,
            slug="area-x",
            ward="ward-x",
            building_count=10,
            expected_visit_count=5,
            case_properties={"lga": "LGA1", "state": "State1"},
            work_area_group=WorkAreaGroupFactory(opportunity=opportunity, name="Group A"),
        )
        client.force_login(org_user_admin)
        response = client.get(self.url(opportunity))

        assert response.status_code == 200
        assert response["Content-Type"] == "text/csv"
        assert f"work_area_summary_{opportunity.opportunity_id}.csv" in response["Content-Disposition"]

        assert set(WorkAreaCSVExporter.FIELD_MAP.keys()) == set(WorkAreaCSVExporter.HEADERS.keys())
        rows = self._parse_csv(response)
        assert rows[0] == list(WorkAreaCSVExporter.HEADERS.values())
        assert rows[1] == [
            "area-x",
            "ward-x",
            f"{wa.centroid.x} {wa.centroid.y}",
            wa.boundary.wkt,
            "10",
            "5",
            "0",
            "LGA1",
            "State1",
            wa.work_area_group.name,
        ]

    @pytest.mark.parametrize(
        "count, expected_rows",
        [
            (0, 1),  # no work areas, only header row
            (3, 4),  # 3 work areas + header
        ],
    )
    def test_row_counts(self, client, org_user_admin, opportunity, count, expected_rows):
        WorkAreaFactory.create_batch(count, opportunity=opportunity)
        client.force_login(org_user_admin)
        rows = self._parse_csv(client.get(self.url(opportunity)))

        assert len(rows) == expected_rows

    def test_null_case_properties_yields_empty_strings(self, client, org_user_admin, opportunity):
        WorkAreaFactory(opportunity=opportunity, case_properties=None, work_area_group=None)
        client.force_login(org_user_admin)
        row = self._parse_csv(client.get(self.url(opportunity)))[1]
        assert row[6:] == ["0", "", "", ""]

    @pytest.mark.parametrize(
        "login_as, method, expected_status",
        [
            ("org_user_member", "get", 404),
            ("org_user_admin", "post", 405),
        ],
    )
    def test_access_denied(self, client, login_as, method, expected_status, request, opportunity):
        user = request.getfixturevalue(login_as)
        client.force_login(user)
        response = getattr(client, method)(self.url(opportunity))
        assert response.status_code == expected_status

    def test_status_filter(self, client, org_user_admin, opportunity):
        WorkAreaFactory(opportunity=opportunity, status=WorkAreaStatus.UNASSIGNED)
        wa = WorkAreaFactory(opportunity=opportunity, status=WorkAreaStatus.NOT_VISITED)
        client.force_login(org_user_admin)

        rows = self._parse_csv(client.get(self.url(opportunity) + f"?status={WorkAreaStatus.NOT_VISITED}"))
        assert rows[1][0] == wa.slug
        assert len(rows) == 2

    def test_excludes_excluded_work_areas(self, client, org_user_admin, opportunity):
        kept = WorkAreaFactory(opportunity=opportunity, slug="kept", status=WorkAreaStatus.NOT_VISITED)
        WorkAreaFactory(opportunity=opportunity, slug="dropped", status=WorkAreaStatus.EXCLUDED)
        client.force_login(org_user_admin)

        rows = self._parse_csv(client.get(self.url(opportunity)))
        assert [r[0] for r in rows[1:]] == [kept.slug]

    def test_assignee_filter(self, client, org_user_admin, opportunity):
        access = OpportunityAccessFactory(opportunity=opportunity)
        wa = WorkAreaFactory(opportunity=opportunity, opportunity_access=access)
        WorkAreaFactory(opportunity=opportunity)  # unassigned
        client.force_login(org_user_admin)

        rows = self._parse_csv(client.get(self.url(opportunity) + f"?assignee={access.user.id}"))
        assert rows[1][0] == wa.slug
        assert len(rows) == 2

    def test_date_filter(self, client, org_user_admin, opportunity):
        wa_with_visit = WorkAreaFactory(opportunity=opportunity)
        wa_without_visit = WorkAreaFactory(opportunity=opportunity)
        UserVisitFactory(
            opportunity=opportunity,
            work_area=wa_with_visit,
            visit_date=datetime(2025, 6, 15, tzinfo=timezone.utc),
        )
        UserVisitFactory(
            opportunity=opportunity,
            work_area=wa_without_visit,
            visit_date=datetime(2025, 3, 1, tzinfo=timezone.utc),
        )
        client.force_login(org_user_admin)

        rows = self._parse_csv(client.get(self.url(opportunity) + "?start_date=2025-06-01&end_date=2025-06-30"))
        assert rows[1][0] == wa_with_visit.slug
        assert len(rows) == 2

    def test_reordered_headers_still_produces_valid_csv(self, client, org_user_admin, opportunity):
        reversed_headers = dict(reversed(list(WorkAreaCSVExporter.HEADERS.items())))

        WorkAreaFactory(
            opportunity=opportunity,
            slug="slug-rev",
            ward="ward-rev",
            building_count=4,
            expected_visit_count=2,
            work_area_group=WorkAreaGroupFactory(opportunity=opportunity, name="Rev Group"),
            case_properties={"lga": "RevLGA", "state": "RevState"},
        )
        client.force_login(org_user_admin)

        with patch.object(WorkAreaCSVExporter, "HEADERS", reversed_headers):
            rows = self._parse_csv(client.get(self.url(opportunity)))
        csv_headers = rows[0]

        expected_headers = list(reversed_headers.values())
        assert csv_headers == expected_headers
        assert len(rows[1]) == len(csv_headers)

        row_dict = dict(zip(csv_headers, rows[1]))
        assert row_dict["Area Slug"] == "slug-rev"
        assert row_dict["Ward"] == "ward-rev"
        assert row_dict["Building Count"] == "4"
        assert row_dict["Expected Visit Count"] == "2"
        assert row_dict["LGA"] == "RevLGA"
        assert row_dict["State"] == "RevState"
        assert row_dict["Work Area Group Name"] == "Rev Group"


@pytest.mark.django_db(transaction=True)
class TestSaveAssignmentNotification(BaseMicroplanningFlagTest):
    @pytest.fixture(autouse=True)
    def setup_microplanning_flag(self, managed_opportunity, request):
        flag, _ = Flag.objects.get_or_create(name=MICROPLANNING)
        flag.opportunities.add(managed_opportunity)
        flag.flush()

    def _url(self, program_manager_org, managed_opportunity):
        return reverse(
            "microplanning:save_assignment",
            kwargs={"org_slug": program_manager_org.slug, "opp_id": managed_opportunity.opportunity_id},
        )

    @patch("commcare_connect.microplanning.views.bulk_create_or_update_cases_by_work_areas")
    def test_schedules_one_notification_per_assignee(
        self, mock_hq_sync, client, program_manager_org, program_manager_org_user_admin, managed_opportunity
    ):
        access_a = OpportunityAccessFactory(opportunity=managed_opportunity)
        access_b = OpportunityAccessFactory(opportunity=managed_opportunity)
        wa1 = WorkAreaFactory(opportunity=managed_opportunity)
        wa2 = WorkAreaFactory(opportunity=managed_opportunity)
        wa3 = WorkAreaFactory(opportunity=managed_opportunity)
        wa4 = WorkAreaFactory(opportunity=managed_opportunity)
        client.force_login(program_manager_org_user_admin)

        payload = {
            "assignments": [
                {"assignee_id": access_a.pk, "work_area_ids": [wa1.id, wa2.id]},
                {"assignee_id": access_a.pk, "work_area_ids": [wa3.id]},
                {"assignee_id": access_b.pk, "work_area_ids": [wa4.id]},
            ]
        }
        with mock.patch(
            "commcare_connect.microplanning.views.send_work_area_assignment_notification.delay"
        ) as delay_patch:
            response = client.post(
                self._url(program_manager_org, managed_opportunity),
                data=json.dumps(payload),
                content_type="application/json",
            )

        assert response.status_code == 200
        called_ids = sorted(call.args[0] for call in delay_patch.call_args_list)
        assert called_ids == sorted([access_a.pk, access_b.pk])

    def test_ignores_assignees_from_other_opportunity(
        self, client, program_manager_org, program_manager_org_user_admin, managed_opportunity
    ):
        other_access = OpportunityAccessFactory(opportunity=OpportunityFactory())
        client.force_login(program_manager_org_user_admin)

        with mock.patch(
            "commcare_connect.microplanning.views.send_work_area_assignment_notification.delay"
        ) as delay_patch:
            response = client.post(
                self._url(program_manager_org, managed_opportunity),
                data=json.dumps({"assignments": [{"assignee_id": other_access.pk, "work_area_ids": [1]}]}),
                content_type="application/json",
            )

        assert response.status_code == 400
        delay_patch.assert_not_called()


@pytest.mark.django_db
class TestReviewInaccessibilityModal(BaseMicroplanningFlagTest):
    def get_url(self, org_slug, opp_id, work_area_id):
        return reverse(
            "microplanning:review_inaccessibility_request",
            kwargs={"org_slug": org_slug, "opp_id": opp_id, "work_area_id": work_area_id},
        )

    def action_url(self, org_slug, opp_id, work_area_id):
        return reverse(
            "microplanning:act_on_inaccessibility_request",
            kwargs={"org_slug": org_slug, "opp_id": opp_id, "work_area_id": work_area_id},
        )

    @pytest.fixture
    def pending_wa(self, opportunity, org_user_admin, mobile_user):
        admin_access = OpportunityAccessFactory(user=org_user_admin, opportunity=opportunity, accepted=True)
        field_worker_access = OpportunityAccessFactory(user=mobile_user, opportunity=opportunity, accepted=True)
        group = WorkAreaGroupFactory(opportunity=opportunity)
        work_area = WorkAreaFactory(
            opportunity=opportunity,
            work_area_group=group,
            opportunity_access=admin_access,
            status=WorkAreaStatus.REQUEST_FOR_INACCESSIBLE,
        )
        inacc_request = WorkAreaInaccessibilityRequestFactory(
            work_area=work_area,
            opportunity_access=field_worker_access,
        )
        return work_area, inacc_request

    def test_get_modal_renders_for_pending_request(self, client, org_user_admin, pending_wa, organization):
        work_area, _ = pending_wa
        client.force_login(org_user_admin)
        url = self.get_url(organization.slug, work_area.opportunity.opportunity_id, work_area.id)
        response = client.get(url)
        assert response.status_code == 200
        assert any(t.name == "microplanning/review_inaccessibility_modal.html" for t in response.templates)

    @pytest.mark.parametrize(
        "status",
        [
            WorkAreaStatus.NOT_VISITED,
            WorkAreaStatus.VISITED,
            WorkAreaStatus.INACCESSIBLE,
        ],
        ids=["not_visited", "visited", "inaccessible"],
    )
    def test_get_modal_404_for_non_pending_status(self, status, client, org_user_admin, opportunity, organization):
        OpportunityAccessFactory(user=org_user_admin, opportunity=opportunity, accepted=True)
        group = WorkAreaGroupFactory(opportunity=opportunity)
        work_area = WorkAreaFactory(opportunity=opportunity, work_area_group=group, status=status)
        client.force_login(org_user_admin)
        url = self.get_url(organization.slug, opportunity.opportunity_id, work_area.id)
        response = client.get(url)
        assert response.status_code == 404

    def test_get_modal_photo_filtered_by_xform_id(self, client, org_user_admin, pending_wa, organization):
        work_area, inacc_request = pending_wa
        BlobMeta.objects.create(
            name="photo.jpg", parent_id=inacc_request.xform_id, content_length=10, content_type="image/jpeg"
        )
        BlobMeta.objects.create(
            name="other.jpg", parent_id="some-other-xform-id", content_length=10, content_type="image/jpeg"
        )
        client.force_login(org_user_admin)
        url = self.get_url(organization.slug, work_area.opportunity.opportunity_id, work_area.id)
        response = client.get(url)
        assert response.status_code == 200
        assert any(t.name == "microplanning/review_inaccessibility_modal.html" for t in response.templates)
        photo = response.context["photo"]
        assert photo is not None
        assert photo.name == "photo.jpg"

    @pytest.mark.parametrize(
        "action, expected_status, expect_notify",
        [
            ("approve", WorkAreaStatus.INACCESSIBLE, False),
            ("deny", WorkAreaStatus.NOT_VISITED, True),
        ],
        ids=["approve", "deny"],
    )
    def test_action_transitions_status(
        self,
        action,
        expected_status,
        expect_notify,
        client,
        org_user_admin,
        pending_wa,
        organization,
        django_capture_on_commit_callbacks,
    ):
        work_area, inacc_request = pending_wa
        client.force_login(org_user_admin)
        url = self.action_url(organization.slug, work_area.opportunity.opportunity_id, work_area.id)

        with (
            patch("commcare_connect.microplanning.views.send_push_notification_task") as mock_notif,
            patch("commcare_connect.microplanning.views.create_or_update_case_by_work_area"),
            django_capture_on_commit_callbacks(execute=True),
        ):
            response = client.post(url, {"action": action})

        assert response.status_code == 204
        work_area.refresh_from_db()
        assert work_area.status == expected_status
        hx_trigger = json.loads(response["HX-Trigger"])
        assert "inaccessibilityReviewed" in hx_trigger
        assert hx_trigger["inaccessibilityReviewed"]["status"] == expected_status

        event = work_area.expected_visit_count_work_area_group_status_opportunity_access_excluded_reason_events.last()
        assert event.pgh_context.metadata["username"] == org_user_admin.username
        assert event.pgh_context.metadata["user_email"] == org_user_admin.email

        if expect_notify:
            mock_notif.delay.assert_called_once()
            notified_user_ids = mock_notif.delay.call_args[0][0]
            assert notified_user_ids == [inacc_request.opportunity_access.user_id]
            assert inacc_request.opportunity_access.user_id != org_user_admin.id
        else:
            mock_notif.delay.assert_not_called()

    def test_action_invalid_action_returns_400(self, client, org_user_admin, pending_wa, organization):
        work_area, _ = pending_wa
        client.force_login(org_user_admin)
        url = self.action_url(organization.slug, work_area.opportunity.opportunity_id, work_area.id)
        response = client.post(url, {"action": "invalid_action"})
        assert response.status_code == 400

    def test_action_hq_sync_failure_does_not_commit_status(self, client, org_user_admin, pending_wa, organization):
        work_area, _ = pending_wa
        client.force_login(org_user_admin)
        url = self.action_url(organization.slug, work_area.opportunity.opportunity_id, work_area.id)
        with patch(
            "commcare_connect.microplanning.views.create_or_update_case_by_work_area",
            side_effect=CommCareHQAPIException("HQ unavailable"),
        ):
            response = client.post(url, {"action": "approve"})
        assert response.status_code == 500
        work_area.refresh_from_db()
        assert work_area.status == WorkAreaStatus.REQUEST_FOR_INACCESSIBLE

    @pytest.mark.parametrize(
        "status",
        [WorkAreaStatus.NOT_VISITED, WorkAreaStatus.INACCESSIBLE],
        ids=["not_visited", "already_inaccessible"],
    )
    def test_action_404_when_wa_not_pending(self, status, client, org_user_admin, opportunity, organization):
        OpportunityAccessFactory(user=org_user_admin, opportunity=opportunity, accepted=True)
        group = WorkAreaGroupFactory(opportunity=opportunity)
        work_area = WorkAreaFactory(opportunity=opportunity, work_area_group=group, status=status)
        client.force_login(org_user_admin)
        url = self.action_url(organization.slug, opportunity.opportunity_id, work_area.id)
        response = client.post(url, {"action": "approve"})
        assert response.status_code == 404

    @pytest.mark.parametrize("setup_microplanning_flag", [False], indirect=True)
    def test_get_modal_microplanning_flag_required(self, client, org_user_admin, opportunity, organization):
        OpportunityAccessFactory(user=org_user_admin, opportunity=opportunity, accepted=True)
        group = WorkAreaGroupFactory(opportunity=opportunity)
        work_area = WorkAreaFactory(
            opportunity=opportunity, work_area_group=group, status=WorkAreaStatus.REQUEST_FOR_INACCESSIBLE
        )
        client.force_login(org_user_admin)
        url = self.get_url(organization.slug, opportunity.opportunity_id, work_area.id)
        response = client.get(url)
        assert response.status_code == 404


@pytest.mark.django_db
class TestSaveAssignment:
    @pytest.fixture(autouse=True)
    def setup_flag(self, managed_opportunity):
        flag, _ = Flag.objects.get_or_create(name=MICROPLANNING)
        flag.opportunities.add(managed_opportunity)
        flag.flush()

    def url(self, org_slug, opp_id):
        return reverse(
            "microplanning:save_assignment",
            kwargs={"org_slug": org_slug, "opp_id": opp_id},
        )

    def _post(self, client, org_slug, opp_id, assignments):
        return client.post(
            self.url(org_slug, opp_id),
            data=json.dumps({"assignments": assignments}),
            content_type="application/json",
        )

    @patch("commcare_connect.microplanning.views.bulk_create_or_update_cases_by_work_areas")
    def test_assigns_work_areas_and_syncs_to_hq(
        self,
        mock_hq_sync,
        client,
        program_manager_org,
        program_manager_org_user_admin,
        managed_opportunity,
    ):
        access = OpportunityAccessFactory(opportunity=managed_opportunity)
        wa1 = WorkAreaFactory(opportunity=managed_opportunity)
        wa2 = WorkAreaFactory(opportunity=managed_opportunity)
        client.force_login(program_manager_org_user_admin)

        response = self._post(
            client,
            program_manager_org.slug,
            managed_opportunity.opportunity_id,
            [{"assignee_id": access.id, "work_area_ids": [wa1.id, wa2.id]}],
        )

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
        mock_hq_sync.assert_called_once()
        synced_ids = {wa.id for wa in mock_hq_sync.call_args[0][0]}
        assert synced_ids == {wa1.id, wa2.id}
        for wa in [wa1, wa2]:
            wa.refresh_from_db()
            assert wa.opportunity_access_id == access.id

    @patch("commcare_connect.microplanning.views.bulk_create_or_update_cases_by_work_areas")
    def test_hq_failure_rolls_back_db(
        self,
        mock_hq_sync,
        client,
        program_manager_org,
        program_manager_org_user_admin,
        managed_opportunity,
    ):
        """If HQ sync fails, the DB assignment must not be committed."""
        mock_hq_sync.side_effect = CommCareHQAPIException("HQ unavailable")
        access = OpportunityAccessFactory(opportunity=managed_opportunity)
        wa = WorkAreaFactory(opportunity=managed_opportunity)
        client.force_login(program_manager_org_user_admin)

        response = self._post(
            client,
            program_manager_org.slug,
            managed_opportunity.opportunity_id,
            [{"assignee_id": access.id, "work_area_ids": [wa.id]}],
        )

        assert response.status_code == 502
        wa.refresh_from_db()
        assert wa.opportunity_access_id is None

    @pytest.mark.parametrize(
        "payload, expected_status",
        [
            ([], 400),
            ([{"assignee_id": 99999, "work_area_ids": [1]}], 400),
        ],
        ids=["empty_assignments", "invalid_assignee"],
    )
    def test_invalid_payload(
        self,
        client,
        program_manager_org,
        program_manager_org_user_admin,
        managed_opportunity,
        payload,
        expected_status,
    ):
        client.force_login(program_manager_org_user_admin)
        response = self._post(client, program_manager_org.slug, managed_opportunity.opportunity_id, payload)
        assert response.status_code == expected_status

    def test_non_program_manager_cannot_assign(self, client, organization, org_user_admin, managed_opportunity):
        access = OpportunityAccessFactory(opportunity=managed_opportunity)
        wa = WorkAreaFactory(opportunity=managed_opportunity)
        client.force_login(org_user_admin)

        response = self._post(
            client,
            organization.slug,
            managed_opportunity.opportunity_id,
            [{"assignee_id": access.id, "work_area_ids": [wa.id]}],
        )
        assert response.status_code == 404


@pytest.mark.django_db
class TestUnassignWorkAreas:
    @pytest.fixture(autouse=True)
    def setup_flag(self, managed_opportunity):
        flag, _ = Flag.objects.get_or_create(name=MICROPLANNING)
        flag.opportunities.add(managed_opportunity)
        flag.flush()

    def url(self, org_slug, opp_id):
        return reverse(
            "microplanning:unassign_work_areas",
            kwargs={"org_slug": org_slug, "opp_id": opp_id},
        )

    def _post(self, client, org_slug, opp_id, work_area_ids):
        return client.post(
            self.url(org_slug, opp_id),
            data=json.dumps({"work_area_ids": work_area_ids}),
            content_type="application/json",
        )

    @patch("commcare_connect.microplanning.views.unassign_work_areas_for_opportunity")
    def test_calls_helper_and_returns_counts(
        self,
        mock_unassign,
        client,
        program_manager_org,
        program_manager_org_user_admin,
        managed_opportunity,
    ):
        access = OpportunityAccessFactory(opportunity=managed_opportunity)
        wa1 = WorkAreaFactory(
            opportunity=managed_opportunity, opportunity_access=access, status=WorkAreaStatus.NOT_VISITED
        )
        wa2 = WorkAreaFactory(
            opportunity=managed_opportunity, opportunity_access=access, status=WorkAreaStatus.NOT_VISITED
        )
        mock_unassign.return_value = {"unassigned_ids": [wa1.id, wa2.id], "skipped": 0, "failed_ids": []}
        client.force_login(program_manager_org_user_admin)

        response = self._post(
            client,
            program_manager_org.slug,
            managed_opportunity.opportunity_id,
            [wa1.id, wa2.id],
        )

        assert response.status_code == 200
        assert response.json() == {
            "status": "ok",
            "unassigned_ids": [wa1.id, wa2.id],
            "skipped": 0,
            "failed_ids": [],
        }
        mock_unassign.assert_called_once()
        kwargs = mock_unassign.call_args.kwargs
        assert kwargs["opportunity"].pk == managed_opportunity.pk
        assert kwargs["work_area_ids"] == [wa1.id, wa2.id]
        assert kwargs["user"] == program_manager_org_user_admin

    @patch("commcare_connect.microplanning.views.unassign_work_areas_for_opportunity")
    def test_all_hq_failures_returns_502(
        self,
        mock_unassign,
        client,
        program_manager_org,
        program_manager_org_user_admin,
        managed_opportunity,
    ):
        wa = WorkAreaFactory(opportunity=managed_opportunity)
        mock_unassign.return_value = {"unassigned_ids": [], "skipped": 0, "failed_ids": [wa.id]}
        client.force_login(program_manager_org_user_admin)

        response = self._post(client, program_manager_org.slug, managed_opportunity.opportunity_id, [wa.id])
        assert response.status_code == 502
        assert "error" in response.json()

    @patch("commcare_connect.microplanning.views.unassign_work_areas_for_opportunity")
    def test_all_skipped_returns_200(
        self,
        mock_unassign,
        client,
        program_manager_org,
        program_manager_org_user_admin,
        managed_opportunity,
    ):
        wa = WorkAreaFactory(opportunity=managed_opportunity)
        mock_unassign.return_value = {"unassigned_ids": [], "skipped": 1, "failed_ids": []}
        client.force_login(program_manager_org_user_admin)

        response = self._post(client, program_manager_org.slug, managed_opportunity.opportunity_id, [wa.id])
        assert response.status_code == 200
        assert response.json() == {"status": "ok", "unassigned_ids": [], "skipped": 1, "failed_ids": []}

    @pytest.mark.parametrize(
        "payload, expected_status",
        [
            ({}, 400),
            ({"work_area_ids": []}, 400),
            ({"work_area_ids": ["abc"]}, 400),
            ({"work_area_ids": [1.9]}, 400),
            ({"work_area_ids": [True]}, 400),
            ({"work_area_ids": [1, 1]}, 400),
        ],
        ids=["missing_key", "empty_list", "str_ids", "float_ids", "bool_ids", "duplicate_ids"],
    )
    def test_invalid_payload(
        self,
        client,
        program_manager_org,
        program_manager_org_user_admin,
        managed_opportunity,
        payload,
        expected_status,
    ):
        client.force_login(program_manager_org_user_admin)
        response = client.post(
            self.url(program_manager_org.slug, managed_opportunity.opportunity_id),
            data=json.dumps(payload),
            content_type="application/json",
        )
        assert response.status_code == expected_status

    def test_invalid_json_body(self, client, program_manager_org, program_manager_org_user_admin, managed_opportunity):
        client.force_login(program_manager_org_user_admin)
        response = client.post(
            self.url(program_manager_org.slug, managed_opportunity.opportunity_id),
            data="not-json",
            content_type="application/json",
        )
        assert response.status_code == 400

    @patch("commcare_connect.microplanning.views.unassign_work_areas_for_opportunity")
    def test_too_many_work_area_ids_returns_400(
        self,
        mock_unassign,
        client,
        program_manager_org,
        program_manager_org_user_admin,
        managed_opportunity,
    ):
        from commcare_connect.microplanning.views import MAX_UNASSIGN_WORK_AREAS

        client.force_login(program_manager_org_user_admin)
        response = client.post(
            self.url(program_manager_org.slug, managed_opportunity.opportunity_id),
            data=json.dumps({"work_area_ids": list(range(1, MAX_UNASSIGN_WORK_AREAS + 2))}),
            content_type="application/json",
        )
        assert response.status_code == 400
        assert str(MAX_UNASSIGN_WORK_AREAS) in response.json()["error"]
        mock_unassign.assert_not_called()

    def test_non_program_manager_blocked(self, client, organization, org_user_admin, managed_opportunity):
        wa = WorkAreaFactory(opportunity=managed_opportunity)
        client.force_login(org_user_admin)

        response = self._post(client, organization.slug, managed_opportunity.opportunity_id, [wa.id])
        assert response.status_code == 404


@pytest.mark.django_db
class TestExcludeWorkAreasView:
    """Thin tests for the view: validation + synchronous exclusion."""

    def url(self, opportunity):
        return reverse(
            "microplanning:exclude_work_areas",
            kwargs={"org_slug": opportunity.organization.slug, "opp_id": opportunity.opportunity_id},
        )

    @patch(
        "commcare_connect.microplanning.views.exclude_work_areas_for_opportunity",
        return_value={"excluded_ids": [1], "skipped": 0, "failed": 0},
    )
    def test_valid_request_calls_exclude_and_returns_200(self, mock_exclude, client, org_user_admin, opportunity):
        wa = WorkAreaFactory(opportunity=opportunity, status=WorkAreaStatus.NOT_VISITED)

        client.force_login(org_user_admin)
        response = client.post(
            self.url(opportunity),
            {"work_area_ids[]": [wa.id], "exclusion_reason": "Flooding"},
        )

        assert response.status_code == 200
        assert "HX-Trigger" in response.headers
        trigger = json.loads(response.headers["HX-Trigger"])
        assert "work_areas_excluded" in trigger
        mock_exclude.assert_called_once()
        kwargs = mock_exclude.call_args.kwargs
        assert kwargs["opportunity"].pk == opportunity.pk
        assert kwargs["work_area_ids"] == [wa.id]
        assert kwargs["user"].pk == org_user_admin.pk
        assert kwargs["exclusion_reason"] == "Flooding"

    @pytest.mark.parametrize(
        "post_data",
        [
            {"work_area_ids[]": [1]},
            {"work_area_ids[]": [1], "exclusion_reason": "   "},
            {"work_area_ids[]": [1], "exclusion_reason": "x" * 501},
        ],
        ids=["missing", "blank", "too_long"],
    )
    @patch("commcare_connect.microplanning.views.exclude_work_areas_for_opportunity")
    def test_invalid_exclusion_reason_returns_400(self, mock_exclude, client, org_user_admin, opportunity, post_data):
        client.force_login(org_user_admin)
        response = client.post(self.url(opportunity), post_data)
        assert response.status_code == 400
        assert "Exclusion reason" in response.json()["error"]
        mock_exclude.assert_not_called()

    @pytest.mark.parametrize(
        "post_data",
        [
            {"exclusion_reason": "Flooding"},
            {"work_area_ids[]": ["abc", "foo"], "exclusion_reason": "Test"},
        ],
        ids=["missing", "non_integer"],
    )
    @patch("commcare_connect.microplanning.views.exclude_work_areas_for_opportunity")
    def test_invalid_work_area_ids_returns_400(self, mock_exclude, client, org_user_admin, opportunity, post_data):
        client.force_login(org_user_admin)
        response = client.post(self.url(opportunity), post_data)
        assert response.status_code == 400
        mock_exclude.assert_not_called()

    @patch("commcare_connect.microplanning.views.exclude_work_areas_for_opportunity")
    def test_too_many_work_area_ids_returns_400(self, mock_exclude, client, org_user_admin, opportunity):
        from commcare_connect.microplanning.views import MAX_EXCLUDE_WORK_AREAS

        client.force_login(org_user_admin)
        response = client.post(
            self.url(opportunity),
            {
                "work_area_ids[]": list(range(1, MAX_EXCLUDE_WORK_AREAS + 2)),
                "exclusion_reason": "Flooding",
            },
        )
        assert response.status_code == 400
        assert str(MAX_EXCLUDE_WORK_AREAS) in response.json()["error"]
        mock_exclude.assert_not_called()


@pytest.mark.django_db
class TestGetMetricsForMicroplanningWorkAreas:
    """Tests for the get_metrics_for_microplanning helper — work area metrics."""

    @pytest.fixture
    def opp(self):
        return OpportunityFactory(end_date=date.today() + timedelta(days=5))

    def _make_work_areas(self, opp, statuses, expected_visit_counts=None):
        """Create WorkArea objects with given statuses (and optional expected_visit_counts list)."""
        areas = []
        for i, status in enumerate(statuses):
            evc = expected_visit_counts[i] if expected_visit_counts else 10
            areas.append(WorkAreaFactory(opportunity=opp, status=status, expected_visit_count=evc))
        return areas

    def _make_visits(self, opp, work_area, *, approved=0, pending=0):
        """Create `approved` approved + `pending` pending UserVisits for `work_area`."""
        for _ in range(approved):
            UserVisitFactory(
                opportunity=opp,
                work_area=work_area,
                status=VisitValidationStatus.approved,
            )
        for _ in range(pending):
            UserVisitFactory(
                opportunity=opp,
                work_area=work_area,
                status=VisitValidationStatus.pending,
            )

    def _get_metric(self, metrics, name):
        result = next((m for m in metrics if m["name"] == name), None)
        assert result is not None, f"Metric '{name}' not found in {[m['name'] for m in metrics]}"
        return result

    def test_days_remaining(self, opp):
        metrics = get_metrics_for_microplanning(opp)
        m = self._get_metric(metrics, "Days Remaining")
        assert m["value"] == 5
        assert "percentage" not in m

    def test_unvisited_count_and_percentage(self, opp):
        """Unvisited = WAs with 0 approved visits, among non-excluded.

        Inaccessible with 0 approved visits is included.
        """
        wa_visited, wa_pending_only, wa_empty, wa_inaccessible, wa_excluded = self._make_work_areas(
            opp,
            [
                WorkAreaStatus.NOT_VISITED,
                WorkAreaStatus.NOT_VISITED,
                WorkAreaStatus.NOT_VISITED,
                WorkAreaStatus.INACCESSIBLE,  # counts as unvisited because 0 approved visits
                WorkAreaStatus.EXCLUDED,
            ],
        )
        self._make_visits(opp, wa_visited, approved=2)
        # Pending visits don't count — these WAs stay "unvisited".
        self._make_visits(opp, wa_pending_only, pending=3)

        metrics = get_metrics_for_microplanning(opp)
        m = self._get_metric(metrics, "Unvisited Work Areas")
        # non_excluded = 4 (wa_visited, wa_pending_only, wa_empty, wa_inaccessible)
        # unvisited = 3 (all non-excluded except wa_visited)
        assert m["value"] == 3
        assert m["percentage"] == 75  # round(3/4 * 100)

    def test_visited_count_and_percentage(self, opp):
        """Visited = WAs with >=1 approved visit, among non-excluded."""
        wa_visited_1, wa_visited_2, wa_no_visits, wa_excluded = self._make_work_areas(
            opp,
            [
                WorkAreaStatus.NOT_VISITED,
                WorkAreaStatus.NOT_VISITED,
                WorkAreaStatus.NOT_VISITED,
                WorkAreaStatus.EXCLUDED,
            ],
        )
        # Two WAs get approved visits; one gets only non-approved (does NOT count as visited).
        self._make_visits(opp, wa_visited_1, approved=1)
        self._make_visits(opp, wa_visited_2, approved=3)
        self._make_visits(opp, wa_no_visits, pending=2)
        # Approved visits on an excluded WA must not bump visited count.
        self._make_visits(opp, wa_excluded, approved=5)

        metrics = get_metrics_for_microplanning(opp)
        m = self._get_metric(metrics, "Visited Work Areas")
        # denominator = 3 non-excluded; numerator = 2 WAs with >=1 approved visit
        assert m["value"] == 2
        assert m["percentage"] == 67  # round(2/3 * 100)

    def test_evc_reached_count_and_percentage(self, opp):
        """EVC reached = WAs with approved_count >= expected_visit_count, among non-excluded."""
        wa_reached, wa_partial, wa_over, wa_excluded_reached = self._make_work_areas(
            opp,
            [
                WorkAreaStatus.NOT_VISITED,
                WorkAreaStatus.NOT_VISITED,
                WorkAreaStatus.NOT_VISITED,
                WorkAreaStatus.EXCLUDED,
            ],
            expected_visit_counts=[5, 5, 5, 5],
        )
        self._make_visits(opp, wa_reached, approved=5)  # reached
        self._make_visits(opp, wa_partial, approved=4)  # not reached
        self._make_visits(opp, wa_over, approved=7)  # reached (>=)
        self._make_visits(opp, wa_excluded_reached, approved=10)  # excluded — ignored

        metrics = get_metrics_for_microplanning(opp)
        m = self._get_metric(metrics, "EVC Reached")
        # non_excluded = 3; reached = 2
        assert m["value"] == 2
        assert m["percentage"] == 67  # round(2/3 * 100)

    def test_inaccessible_count_and_percentage(self, opp):
        self._make_work_areas(
            opp,
            [
                WorkAreaStatus.INACCESSIBLE,
                WorkAreaStatus.VISITED,
                WorkAreaStatus.EXCLUDED,
            ],
        )
        metrics = get_metrics_for_microplanning(opp)
        m = self._get_metric(metrics, "Inaccessible Work Areas")
        assert m["value"] == 1
        assert m["percentage"] == 50  # round(1/2 * 100)

    def test_excluded_count_and_percentage(self, opp):
        self._make_work_areas(
            opp,
            [
                WorkAreaStatus.EXCLUDED,
                WorkAreaStatus.EXCLUDED,
                WorkAreaStatus.VISITED,
            ],
        )
        metrics = get_metrics_for_microplanning(opp)
        m = self._get_metric(metrics, "Excluded Work Areas")
        assert m["value"] == 2
        assert m["percentage"] == 67  # round(2/3 * 100)

    def test_pct_visited_to_pct_visits(self, opp):
        """Ratio uses approved UserVisits on non-excluded WAs only, and data-driven `visited` count.

        Setup:
          - wa_visited (NOT_VISITED, expected=10): 1 approved → counted as visited
          - wa_unvisited (NOT_VISITED, expected=10): 0 approved → not visited
          - wa_excluded (EXCLUDED, expected=10): 3 approved → excluded from both numerator and denominator
          pct_wa_visited = 1/2 = 0.5  (non_excluded = 2)
          total_approved (non_excluded) = 1
          total_expected (non_excluded) = 10 + 10 = 20
          pct_visits = 1/20 = 0.05
          ratio = 0.5 / 0.05 = 10.0
        """
        wa_visited, wa_unvisited, wa_excluded = self._make_work_areas(
            opp,
            [WorkAreaStatus.NOT_VISITED, WorkAreaStatus.NOT_VISITED, WorkAreaStatus.EXCLUDED],
            expected_visit_counts=[10, 10, 10],
        )
        # 1 approved on visited WA
        self._make_visits(opp, wa_visited, approved=1)
        # Approved visits on excluded WAs must be ignored.
        self._make_visits(opp, wa_excluded, approved=3)
        # Non-approved noise must be ignored.
        self._make_visits(opp, wa_unvisited, pending=5)
        UserVisitFactory(opportunity=opp, work_area=None, status=VisitValidationStatus.pending)

        metrics = get_metrics_for_microplanning(opp)
        m = self._get_metric(metrics, "% WA visited to % total visits")
        assert m["value"] == 1000.0
        assert "percentage" not in m
        assert m["unit"] == "%"

    def test_pct_visited_to_pct_visits_zero_denominator(self, opp):
        """No visits and no expected visits → show '--'."""

        metrics = get_metrics_for_microplanning(opp)
        m = self._get_metric(metrics, "% WA visited to % total visits")
        assert m["value"] == "--"
        assert m["unit"] == "%"

    def test_pct_visited_ignores_visits_without_work_area(self, opp):
        """Approved visits with no work_area must not inflate the ratio's total_approved denominator."""
        wa_visited, wa_unvisited = self._make_work_areas(
            opp,
            [WorkAreaStatus.NOT_VISITED, WorkAreaStatus.NOT_VISITED],
            expected_visit_counts=[10, 10],
        )
        self._make_visits(opp, wa_visited, approved=1)
        # Orphan approved visits (no work area) must be ignored.
        for _ in range(3):
            UserVisitFactory(opportunity=opp, work_area=None, status=VisitValidationStatus.approved)

        metrics = get_metrics_for_microplanning(opp)
        m = self._get_metric(metrics, "% WA visited to % total visits")
        # total_approved (WA-attached) = 1 → pct_visits = 1/20 = 0.05
        # pct_wa_visited = 1/2 = 0.5 → ratio = 0.5 / 0.05 = 10.0
        assert m["value"] == 1000.0

    def test_zero_non_excluded_work_areas(self, opp):
        """All WAs excluded → percentage metrics show None; visited count is 0."""
        self._make_work_areas(opp, [WorkAreaStatus.EXCLUDED])
        metrics = get_metrics_for_microplanning(opp)
        m = self._get_metric(metrics, "Visited Work Areas")
        assert m["value"] == 0
        assert m["percentage"] is None

    def test_unassigned_counted_as_unvisited(self, opp):
        """UNASSIGNED with 0 approved visits → unvisited. A WA with approved visits → visited regardless of status."""
        wa_unassigned, wa_with_visits = self._make_work_areas(
            opp,
            [
                WorkAreaStatus.UNASSIGNED,  # default status, no visits → unvisited
                WorkAreaStatus.UNASSIGNED,  # has approved visit → visited
            ],
        )
        self._make_visits(opp, wa_with_visits, approved=1)

        metrics = get_metrics_for_microplanning(opp)
        m = self._get_metric(metrics, "Unvisited Work Areas")
        assert m["value"] == 1
        assert m["percentage"] == 50

    @pytest.mark.parametrize(
        "end_date, expected",
        [
            pytest.param(None, "--", id="missing"),
            pytest.param(date.today() - timedelta(days=3), 0, id="in-past"),
        ],
    )
    def test_days_remaining_edge_cases(self, opp, end_date, expected):
        """Missing end_date → '--'; past end_date → 0 (not negative)."""
        opp.end_date = end_date
        metrics = get_metrics_for_microplanning(opp)
        m = self._get_metric(metrics, "Days Remaining")
        assert m["value"] == expected


@pytest.mark.django_db
class TestCoverageProgressView(BaseMicroplanningFlagTest):
    def url(self, org_slug, opp_id):
        return reverse("microplanning:coverage_progress", args=(org_slug, opp_id))

    def test_renders_page_with_tables_in_context(self, client, org_user_admin, opportunity):
        WorkAreaFactory(opportunity=opportunity, ward="w1", status=WorkAreaStatus.VISITED)
        client.force_login(org_user_admin)
        resp = client.get(self.url(opportunity.organization.slug, str(opportunity.opportunity_id)))
        assert resp.status_code == 200
        assert "microplanning/coverage_progress.html" in {t.name for t in resp.templates}
        assert set(resp.context["header"].keys()) == {"ward_saturation_goal"}
        ward_table = resp.context["ward_table"]
        assert any(row.get_cell_value("ward") == "w1" for row in ward_table.rows)
        assert "wag_table" in resp.context

    def test_context_exposes_date_filter(self, client, org_user_admin, opportunity):
        WorkAreaFactory(opportunity=opportunity, ward="w1", status=WorkAreaStatus.VISITED)
        client.force_login(org_user_admin)
        resp = client.get(self.url(opportunity.organization.slug, str(opportunity.opportunity_id)))
        assert resp.status_code == 200
        assert "filter_form" in resp.context
        # No filter applied -> the download links carry no filter params.
        assert resp.context["export_hrefs"]["ward"]["csv"] == "?export=csv&table=ward"

    def test_export_links_carry_custom_range(self, client, org_user_admin, opportunity):
        WorkAreaFactory(opportunity=opportunity, ward="w1", status=WorkAreaStatus.VISITED)
        client.force_login(org_user_admin)
        resp = client.get(
            self.url(opportunity.organization.slug, str(opportunity.opportunity_id)),
            {"start": "2026-01-01", "end": "2026-01-31"},
        )
        assert resp.context["export_hrefs"]["wag"]["xlsx"] == (
            "?start=2026-01-01&end=2026-01-31&export=xlsx&table=wag"
        )

    def test_single_date_shows_validation_error(self, client, org_user_admin, opportunity):
        WorkAreaFactory(opportunity=opportunity, ward="w1", status=WorkAreaStatus.VISITED)
        client.force_login(org_user_admin)
        # Only a From date -> the page renders an error and falls back to overall (links carry no dates).
        resp = client.get(
            self.url(opportunity.organization.slug, str(opportunity.opportunity_id)),
            {"start": "2026-01-01"},
        )
        assert resp.status_code == 200
        assert b"Select both a From and a To date" in resp.content
        assert resp.context["export_hrefs"]["ward"]["csv"] == "?export=csv&table=ward"

    def test_export_honors_date_filter_params(self, client, org_user_admin, opportunity):
        WorkAreaFactory(opportunity=opportunity, ward="w1", status=WorkAreaStatus.VISITED)
        client.force_login(org_user_admin)
        resp = client.get(
            self.url(opportunity.organization.slug, str(opportunity.opportunity_id)),
            {"start": "2026-01-01", "end": "2026-01-31", "export": "csv", "table": "ward"},
        )
        assert resp.status_code == 200
        assert resp["Content-Type"].startswith("text/csv")

    def test_export_returns_csv_of_requested_table(self, client, org_user_admin, opportunity):
        WorkAreaFactory(opportunity=opportunity, ward="w1", status=WorkAreaStatus.VISITED)
        client.force_login(org_user_admin)
        resp = client.get(
            self.url(opportunity.organization.slug, str(opportunity.opportunity_id)),
            {"export": "csv", "table": "ward"},
        )
        assert resp.status_code == 200
        assert resp["Content-Type"].startswith("text/csv")
        assert "attachment" in resp["Content-Disposition"]
        body = resp.getvalue().decode()
        assert "Ward Population Target" in body
        assert "w1" in body

    def test_export_returns_xlsx_of_wag_table(self, client, org_user_admin, opportunity):
        group = WorkAreaGroupFactory(opportunity=opportunity, ward="w1", name="G1")
        WorkAreaFactory(opportunity=opportunity, ward="w1", work_area_group=group, status=WorkAreaStatus.VISITED)
        client.force_login(org_user_admin)
        resp = client.get(
            self.url(opportunity.organization.slug, str(opportunity.opportunity_id)),
            {"export": "xlsx", "table": "wag"},
        )
        assert resp.status_code == 200
        assert "spreadsheetml" in resp["Content-Type"]  # .xlsx
        assert "metrics_by_work_area_group.xlsx" in resp["Content-Disposition"]

    def test_export_unknown_table_returns_400(self, client, org_user_admin, opportunity):
        client.force_login(org_user_admin)
        resp = client.get(
            self.url(opportunity.organization.slug, str(opportunity.opportunity_id)),
            {"export": "csv", "table": "bogus"},
        )
        assert resp.status_code == 400

    def test_statement_timeout_degrades_gracefully(self, client, org_user_admin, opportunity):
        client.force_login(org_user_admin)
        cause = Exception()
        cause.pgcode = "57014"  # QueryCanceled — what statement_timeout raises
        timeout_error = OperationalError("canceling statement due to statement timeout")
        timeout_error.__cause__ = cause
        with (
            patch("commcare_connect.microplanning.views.CoverageProgressReport") as report_cls,
            patch("commcare_connect.microplanning.views.transaction.set_rollback") as set_rollback,
        ):
            report_cls.return_value.header.side_effect = timeout_error
            resp = client.get(self.url(opportunity.organization.slug, str(opportunity.opportunity_id)))
        assert resp.status_code == 503
        # The degraded response must be query-free: a base.html render would re-hit the aborted txn.
        assert resp["Content-Type"].startswith("text/plain")
        assert b"timed out" in resp.content
        set_rollback.assert_called_once_with(True)

    def test_non_timeout_operational_error_is_not_masked(self, client, org_user_admin, opportunity):
        client.force_login(org_user_admin)
        with patch("commcare_connect.microplanning.views.CoverageProgressReport") as report_cls:
            report_cls.return_value.header.side_effect = OperationalError("connection lost")  # no pgcode 57014
            with pytest.raises(OperationalError):
                client.get(self.url(opportunity.organization.slug, str(opportunity.opportunity_id)))
