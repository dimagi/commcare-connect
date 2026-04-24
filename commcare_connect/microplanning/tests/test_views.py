from __future__ import annotations

import csv as csv_mod
import io
import json
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
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
from commcare_connect.microplanning.views import UserVisitVectorLayer, get_metrics_for_microplanning
from commcare_connect.opportunity.models import VisitValidationStatus
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
        work_area = WorkAreaFactory(opportunity=opportunity, expected_visit_count=10)

        initial_event_count = work_area.expected_visit_count_work_area_group_excluded_reason_events.count()
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

        events = work_area.expected_visit_count_work_area_group_excluded_reason_events
        assert events.count() == initial_event_count + 1
        event = events.last()
        assert event.pgh_context.metadata["reason"] == "Boundary adjusted"
        assert event.expected_visit_count == new_expected_visit_count
        assert event.work_area_group == group

    @patch("commcare_connect.microplanning.views.create_or_update_case_by_work_area")
    def test_no_history_created_when_nothing_changes(self, mock_sync, client, org_user_admin, opportunity):
        group = WorkAreaGroupFactory(opportunity=opportunity)
        work_area = WorkAreaFactory(opportunity=opportunity, expected_visit_count=10, work_area_group=group)
        initial_event_count = work_area.expected_visit_count_work_area_group_excluded_reason_events.count()

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
        assert work_area.expected_visit_count_work_area_group_excluded_reason_events.count() == initial_event_count
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
        work_area = WorkAreaFactory(opportunity=opportunity, expected_visit_count=10, work_area_group=group)
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
        group = WorkAreaGroupFactory(opportunity=opportunity, opportunity_access=access)
        wa_assigned = WorkAreaFactory(
            opportunity=opportunity, work_area_group=group, status=WorkAreaStatus.NOT_STARTED
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
        group = WorkAreaGroupFactory(opportunity=opportunity, opportunity_access=access)
        WorkAreaFactory(opportunity=opportunity, work_area_group=group)

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
        group = WorkAreaGroupFactory(opportunity=opportunity, opportunity_access=access)

        wa_not_started = WorkAreaFactory(
            opportunity=opportunity, work_area_group=group, status=WorkAreaStatus.NOT_STARTED
        )
        wa_visited = WorkAreaFactory(opportunity=opportunity, work_area_group=group, status=WorkAreaStatus.VISITED)
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
        group = WorkAreaGroupFactory(opportunity=opportunity, opportunity_access=access)
        work_area = WorkAreaFactory(opportunity=opportunity, work_area_group=group)
        return SimpleNamespace(access=access, group=group, work_area=work_area)

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
        other_group = WorkAreaGroupFactory(opportunity=opportunity, opportunity_access=other_access)
        other_wa = WorkAreaFactory(opportunity=opportunity, work_area_group=other_group)
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
        other_group = WorkAreaGroupFactory(opportunity=opportunity, opportunity_access=other_access)
        other_wa = WorkAreaFactory(
            opportunity=opportunity, work_area_group=other_group, status=WorkAreaStatus.NOT_STARTED
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

    def test_excludes_excluded_work_areas(self, client, org_user_admin, opportunity):
        kept = WorkAreaFactory(opportunity=opportunity, slug="kept", status=WorkAreaStatus.NOT_STARTED)
        WorkAreaFactory(opportunity=opportunity, slug="dropped", status=WorkAreaStatus.EXCLUDED)
        client.force_login(org_user_admin)

        rows = self._parse_csv(client.get(self.url(opportunity)))
        assert [r[0] for r in rows[1:]] == [kept.slug]

    def test_assignee_filter(self, client, org_user_admin, opportunity):
        access = OpportunityAccessFactory(opportunity=opportunity)
        group = WorkAreaGroupFactory(opportunity=opportunity, opportunity_access=access)
        wa = WorkAreaFactory(opportunity=opportunity, work_area_group=group)
        WorkAreaFactory(opportunity=opportunity)  # unassigned, no group
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


@pytest.mark.django_db
class TestExcludeWorkAreas:
    def url(self, opportunity):
        return reverse(
            "microplanning:exclude_work_areas",
            kwargs={"org_slug": opportunity.organization.slug, "opp_id": opportunity.opportunity_id},
        )

    @patch("commcare_connect.microplanning.views.create_or_update_case")
    def test_happy_path_excludes_not_started_areas(self, mock_hq, client, org_user_admin, opportunity):
        access = OpportunityAccessFactory(opportunity=opportunity)
        group = WorkAreaGroupFactory(opportunity=opportunity, opportunity_access=access)
        work_areas = WorkAreaFactory.create_batch(
            2, opportunity=opportunity, status=WorkAreaStatus.NOT_STARTED, work_area_group=group
        )
        client.force_login(org_user_admin)
        response = client.post(
            self.url(opportunity),
            {"work_area_ids[]": [wa.id for wa in work_areas], "exclusion_reason": "Flooding"},
        )

        assert response.status_code == 200
        data = response.json()
        assert set(data["excluded"]) == {wa.id for wa in work_areas}
        assert data["skipped"] == []
        assert data["failed"] == []

        for wa in work_areas:
            wa.refresh_from_db()
            assert wa.status == WorkAreaStatus.EXCLUDED
            assert wa.work_area_group is None
            assert wa.excluded_by == org_user_admin
            assert wa.excluded_reason == "Flooding"

        assert mock_hq.call_count == 2

    @patch("commcare_connect.microplanning.views.create_or_update_case")
    def test_mixed_batch(self, mock_hq, client, org_user_admin, opportunity):
        wa_valid = WorkAreaFactory(opportunity=opportunity, status=WorkAreaStatus.NOT_STARTED)
        wa_inaccessible = WorkAreaFactory(opportunity=opportunity, status=WorkAreaStatus.INACCESSIBLE)
        wa_excluded = WorkAreaFactory(opportunity=opportunity, status=WorkAreaStatus.EXCLUDED)

        client.force_login(org_user_admin)
        response = client.post(
            self.url(opportunity),
            {
                "work_area_ids[]": [wa_valid.id, wa_inaccessible.id, wa_excluded.id],
                "exclusion_reason": "Test",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["excluded"] == [wa_valid.id]
        skipped_ids = {s["id"] for s in data["skipped"]}
        assert skipped_ids == {wa_inaccessible.id, wa_excluded.id}
        skipped_reasons = {s["id"]: s["reason"] for s in data["skipped"]}
        assert skipped_reasons[wa_inaccessible.id] == "inaccessible"
        assert skipped_reasons[wa_excluded.id] == "already_excluded"

        wa_valid.refresh_from_db()
        assert wa_valid.status == WorkAreaStatus.EXCLUDED

    @patch("commcare_connect.microplanning.views.create_or_update_case")
    def test_hq_failure_rolls_back_single_item(self, mock_hq, client, org_user_admin, opportunity):
        access = OpportunityAccessFactory(opportunity=opportunity)
        group = WorkAreaGroupFactory(opportunity=opportunity, opportunity_access=access)
        wa_ok = WorkAreaFactory(opportunity=opportunity, status=WorkAreaStatus.NOT_STARTED, work_area_group=group)
        wa_fail = WorkAreaFactory(opportunity=opportunity, status=WorkAreaStatus.NOT_STARTED, work_area_group=group)

        def hq_side_effect(*args, **kwargs):
            # fail only for wa_fail's case_id
            if kwargs.get("case_id") == str(wa_fail.case_id):
                raise CommCareHQAPIException("HQ down")
            return MagicMock()

        mock_hq.side_effect = hq_side_effect

        client.force_login(org_user_admin)
        response = client.post(
            self.url(opportunity),
            {"work_area_ids[]": [wa_ok.id, wa_fail.id], "exclusion_reason": "Test"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["excluded"] == [wa_ok.id]
        assert len(data["failed"]) == 1
        assert data["failed"][0]["id"] == wa_fail.id

        wa_ok.refresh_from_db()
        assert wa_ok.status == WorkAreaStatus.EXCLUDED

        wa_fail.refresh_from_db()
        assert wa_fail.status == WorkAreaStatus.NOT_STARTED  # rolled back
        assert wa_fail.work_area_group == group  # group assignment was also rolled back

    @patch("commcare_connect.microplanning.views.create_or_update_case")
    def test_no_case_id_excludes_locally_without_hq_call(self, mock_hq, client, org_user_admin, opportunity):
        wa = WorkAreaFactory(opportunity=opportunity, status=WorkAreaStatus.NOT_STARTED, case_id=None)

        client.force_login(org_user_admin)
        response = client.post(
            self.url(opportunity),
            {"work_area_ids[]": [wa.id], "exclusion_reason": "No case"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["excluded"] == [wa.id]
        mock_hq.assert_not_called()
        wa.refresh_from_db()
        assert wa.status == WorkAreaStatus.EXCLUDED


@pytest.mark.django_db
class TestExcludeWorkAreasValidation:
    def url(self, opportunity):
        return reverse(
            "microplanning:exclude_work_areas",
            kwargs={"org_slug": opportunity.organization.slug, "opp_id": opportunity.opportunity_id},
        )

    @pytest.mark.parametrize(
        "post_data",
        [
            {"work_area_ids[]": [1]},
            {"work_area_ids[]": [1], "exclusion_reason": "   "},
            {"work_area_ids[]": [1], "exclusion_reason": "x" * 501},
        ],
        ids=["missing", "blank", "too_long"],
    )
    def test_invalid_exclusion_reason_returns_400(self, client, org_user_admin, opportunity, post_data):
        client.force_login(org_user_admin)
        response = client.post(self.url(opportunity), post_data)
        assert response.status_code == 400
        assert "exclusion_reason" in response.json()["error"]

    @pytest.mark.parametrize(
        "post_data",
        [
            {"exclusion_reason": "Flooding"},
            {"work_area_ids[]": ["abc", "foo"], "exclusion_reason": "Test"},
        ],
        ids=["missing", "non_integer"],
    )
    def test_invalid_work_area_ids_returns_400(self, client, org_user_admin, opportunity, post_data):
        client.force_login(org_user_admin)
        response = client.post(self.url(opportunity), post_data)
        assert response.status_code == 400

    def test_work_area_from_other_opportunity_is_skipped(self, client, org_user_admin, opportunity):
        other_wa = WorkAreaFactory(status=WorkAreaStatus.NOT_STARTED)  # different opportunity
        client.force_login(org_user_admin)
        response = client.post(
            self.url(opportunity),
            {"work_area_ids[]": [other_wa.id], "exclusion_reason": "Test"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["excluded"] == []
        assert data["skipped"][0]["reason"] == "not_found"

    @pytest.mark.parametrize(
        "status",
        [
            WorkAreaStatus.VISITED,
            WorkAreaStatus.NOT_VISITED,
            WorkAreaStatus.UNASSIGNED,
            WorkAreaStatus.REQUEST_FOR_INACCESSIBLE,
            WorkAreaStatus.EXPECTED_VISIT_REACHED,
        ],
    )
    def test_work_started_statuses_are_skipped(self, client, org_user_admin, opportunity, status):
        wa = WorkAreaFactory(opportunity=opportunity, status=status)
        client.force_login(org_user_admin)
        response = client.post(
            self.url(opportunity),
            {"work_area_ids[]": [wa.id], "exclusion_reason": "Test"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["skipped"][0]["reason"] == "work_started"
        wa.refresh_from_db()
        assert wa.status == status  # unchanged


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
                WorkAreaStatus.NOT_STARTED,
                WorkAreaStatus.NOT_STARTED,
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
                WorkAreaStatus.NOT_STARTED,
                WorkAreaStatus.NOT_STARTED,
                WorkAreaStatus.NOT_STARTED,
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
                WorkAreaStatus.NOT_STARTED,
                WorkAreaStatus.NOT_STARTED,
                WorkAreaStatus.NOT_STARTED,
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
        """Ratio uses approved UserVisits only, and data-driven `visited` count.

        Setup:
          - wa_visited (NOT_STARTED, expected=10): 1 approved → counted as visited
          - wa_unvisited (NOT_STARTED, expected=10): 0 approved → not visited
          - wa_excluded (EXCLUDED, expected=10): 3 approved → excluded from denominator
          pct_wa_visited = 1/2 = 0.5  (non_excluded = 2)
          total_approved = 1 + 0 + 3 = 4
          total_expected (non_excluded) = 10 + 10 = 20
          pct_visits = 4/20 = 0.2
          ratio = 0.5 / 0.2 = 2.5
        """
        wa_visited, wa_unvisited, wa_excluded = self._make_work_areas(
            opp,
            [WorkAreaStatus.NOT_STARTED, WorkAreaStatus.NOT_STARTED, WorkAreaStatus.EXCLUDED],
            expected_visit_counts=[10, 10, 10],
        )
        # 1 approved on visited WA
        self._make_visits(opp, wa_visited, approved=1)
        # 3 approved on excluded WA (boosts total_approved without affecting non_excluded denominator)
        self._make_visits(opp, wa_excluded, approved=3)
        # Non-approved noise must be ignored.
        self._make_visits(opp, wa_unvisited, pending=5)
        UserVisitFactory(opportunity=opp, work_area=None, status=VisitValidationStatus.pending)

        metrics = get_metrics_for_microplanning(opp)
        m = self._get_metric(metrics, "% WA visited to % total visits")
        assert m["value"] == 250
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
            [WorkAreaStatus.NOT_STARTED, WorkAreaStatus.NOT_STARTED],
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

    def test_end_date_missing(self, opp):
        """No end_date → Days Remaining shows '--'."""

        opp.end_date = None
        metrics = get_metrics_for_microplanning(opp)
        m = self._get_metric(metrics, "Days Remaining")
        assert m["value"] == "--"

    def test_end_date_in_past(self, opp):
        """Past end_date → Days Remaining is 0, not negative."""

        opp.end_date = date.today() - timedelta(days=3)
        metrics = get_metrics_for_microplanning(opp)
        m = self._get_metric(metrics, "Days Remaining")
        assert m["value"] == 0
