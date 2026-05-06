from __future__ import annotations

import csv as csv_mod
import io
import json
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest import mock
from unittest.mock import MagicMock, patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from commcare_connect.flags.flag_names import MICROPLANNING
from commcare_connect.flags.models import Flag
from commcare_connect.microplanning import views as microplanning_views
from commcare_connect.microplanning.filters import WorkAreaMapFilterSet
from commcare_connect.microplanning.models import WorkArea, WorkAreaStatus
from commcare_connect.microplanning.tasks import WorkAreaCSVExporter
from commcare_connect.microplanning.tests.factories import WorkAreaFactory, WorkAreaGroupFactory
from commcare_connect.microplanning.views import UserVisitVectorLayer
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
class TestGetMetricsForMicroplanning:
    def test_end_date_missing(self):
        opp = SimpleNamespace(end_date=None)
        metrics = microplanning_views.get_metrics_for_microplanning(opp)
        assert metrics == [{"name": "Days Remaining", "value": "--"}]

    def test_end_date_in_future(self):
        with mock.patch.object(microplanning_views, "localdate", return_value=date(2026, 1, 1)):
            opp = SimpleNamespace(end_date=date(2026, 1, 11))
            metrics = microplanning_views.get_metrics_for_microplanning(opp)
            assert metrics == [{"name": "Days Remaining", "value": 10}]

    def test_end_date_in_past(self):
        with mock.patch.object(microplanning_views, "localdate", return_value=date(2026, 1, 1)):
            opp = SimpleNamespace(end_date=date(2025, 12, 30))
            metrics = microplanning_views.get_metrics_for_microplanning(opp)
            assert metrics == [{"name": "Days Remaining", "value": 0}]


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

        initial_event_count = work_area.expected_visit_count_work_area_group_status_opportunity_access_events.count()
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

        events = work_area.expected_visit_count_work_area_group_status_opportunity_access_events
        assert events.count() == initial_event_count + 1
        event = events.last()
        assert event.pgh_context.metadata["reason"] == "Boundary adjusted"
        assert event.expected_visit_count == new_expected_visit_count
        assert event.work_area_group == group

    @patch("commcare_connect.microplanning.views.create_or_update_case_by_work_area")
    def test_no_history_created_when_nothing_changes(self, mock_sync, client, org_user_admin, opportunity):
        group = WorkAreaGroupFactory(opportunity=opportunity)
        work_area = WorkAreaFactory(opportunity=opportunity, expected_visit_count=10, work_area_group=group)
        initial_event_count = work_area.expected_visit_count_work_area_group_status_opportunity_access_events.count()

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
            work_area.expected_visit_count_work_area_group_status_opportunity_access_events.count()
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
        WorkAreaFactory(opportunity=opportunity, status=WorkAreaStatus.NOT_STARTED)
        qs = self._get_tile_queryset(client, org_user_admin, opportunity)
        assert qs.count() == 2

    def test_status_filter_forwarded(self, client, org_user_admin, opportunity):
        WorkAreaFactory(opportunity=opportunity, status=WorkAreaStatus.VISITED)
        wa_not_started = WorkAreaFactory(opportunity=opportunity, status=WorkAreaStatus.NOT_STARTED)
        qs = self._get_tile_queryset(
            client,
            org_user_admin,
            opportunity,
            query_params={"status": WorkAreaStatus.NOT_STARTED},
        )
        assert list(qs.values_list("id", flat=True)) == [wa_not_started.id]

    def test_assignee_filter_forwarded(self, client, org_user_admin, opportunity):
        access = OpportunityAccessFactory(opportunity=opportunity)
        wa_assigned = WorkAreaFactory(
            opportunity=opportunity, opportunity_access=access, status=WorkAreaStatus.NOT_STARTED
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

        wa_not_started = WorkAreaFactory(
            opportunity=opportunity,
            work_area_group=group,
            opportunity_access=access,
            status=WorkAreaStatus.NOT_STARTED,
        )
        wa_visited = WorkAreaFactory(
            opportunity=opportunity, work_area_group=group, opportunity_access=access, status=WorkAreaStatus.VISITED
        )
        wa_unassigned = WorkAreaFactory(opportunity=opportunity, status=WorkAreaStatus.UNASSIGNED)
        return SimpleNamespace(
            access=access,
            group=group,
            wa_not_started=wa_not_started,
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
            ([WorkAreaStatus.NOT_STARTED, WorkAreaStatus.UNASSIGNED], ["wa_not_started", "wa_unassigned"]),
        ],
        ids=["single_status", "multiple_statuses"],
    )
    def test_status_filter(self, opportunity, work_areas, statuses, expected_attrs):
        expected = {getattr(work_areas, attr).id for attr in expected_attrs}
        assert self._filter_ids({"status": statuses}, opportunity) == expected

    def test_assignee_filter_excludes_unassigned(self, opportunity, work_areas):
        result = self._filter_ids({"assignee": [work_areas.access.user.pk]}, opportunity)
        assert result == {work_areas.wa_not_started.id, work_areas.wa_visited.id}

    @pytest.mark.parametrize(
        "params, expected_attrs",
        [
            ({"start_date": "2026-03-15"}, ["wa_not_started"]),
            ({"end_date": "2026-03-15"}, ["wa_visited"]),
            ({"start_date": "2026-03-15", "end_date": "2026-03-22"}, ["wa_not_started"]),
        ],
        ids=["start_date_gte", "end_date_lte", "date_range"],
    )
    def test_date_filters(self, opportunity, work_areas, params, expected_attrs):
        for wa_attr, visit_date in [("wa_visited", "2026-03-10"), ("wa_not_started", "2026-03-20")]:
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
            {"status": [WorkAreaStatus.NOT_STARTED], "assignee": [work_areas.access.user.pk]}, opportunity
        )
        assert result == {work_areas.wa_not_started.id}

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
            opportunity=opportunity, opportunity_access=other_access, status=WorkAreaStatus.NOT_STARTED
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
            case_properties={"max_wag": "3", "wag_serial_number": "42", "lga": "LGA1", "state": "State1"},
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
            "3",
            "42",
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
        assert row[6:] == ["", "", "", "", ""]

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
        wa = WorkAreaFactory(opportunity=opportunity, status=WorkAreaStatus.NOT_STARTED)
        client.force_login(org_user_admin)

        rows = self._parse_csv(client.get(self.url(opportunity) + f"?status={WorkAreaStatus.NOT_STARTED}"))
        assert rows[1][0] == wa.slug
        assert len(rows) == 2

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
            case_properties={"max_wag": "1", "wag_serial_number": "77", "lga": "RevLGA", "state": "RevState"},
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
        assert row_dict["Max WAG"] == "1"
        assert row_dict["WAG Serial Number"] == "77"
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
