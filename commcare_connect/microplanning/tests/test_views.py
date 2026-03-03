from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest import mock
from unittest.mock import MagicMock, patch

import pytest
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from commcare_connect.flags.flag_names import MICROPLANNING
from commcare_connect.flags.models import Flag
from commcare_connect.microplanning import views as microplanning_views


@pytest.mark.django_db
class TestWorkAreaUpload:
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

        # Flag opportunity as allowed
        flag, _ = Flag.objects.get_or_create(name=MICROPLANNING)
        flag.opportunities.add(opportunity)
        flag.save()
        cache.clear()

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

    @patch("commcare_connect.microplanning.views.import_work_areas_task.delay")
    def test_flagged_permission_required(self, mock_delay, client, org_user_admin, opportunity, csv_file):
        """
        Ensure upload is only allowed if the opportunity is flagged for microplanning.
        """
        url = self.get_url(opportunity.organization.slug, opportunity.opportunity_id)
        client.force_login(org_user_admin)
        # Ensure opportunity is NOT flagged
        Flag.objects.filter(name=MICROPLANNING).delete()
        cache.clear()

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
class TestMicroplanningHomeView:
    def url(self, org_slug: str, opp_id: str):
        return reverse("microplanning:microplanning_home", args=(org_slug, opp_id))

    def test_success(self, client: Client, settings, organization, org_user_admin, opportunity):
        settings.MAPBOX_TOKEN = "test-mapbox-token"
        cache.clear()
        flag, _ = Flag.objects.get_or_create(name=MICROPLANNING)
        flag.opportunities.add(opportunity)

        client.force_login(org_user_admin)
        response = client.get(self.url(organization.slug, str(opportunity.opportunity_id)))

        assert response.status_code == 200
        assert any(t.name == "microplanning/home.html" for t in response.templates)

    def test_flag_disabled(self, client: Client, organization, org_user_admin, opportunity):
        client.force_login(org_user_admin)
        response = client.get(self.url(organization.slug, str(opportunity.opportunity_id)))
        assert response.status_code == 404

    def test_unauthenticated(self, client: Client, organization, org_user_member, opportunity):
        flag, _ = Flag.objects.get_or_create(name=MICROPLANNING)
        flag.opportunities.add(opportunity)

        client.force_login(org_user_member)
        response = client.get(self.url(organization.slug, str(opportunity.opportunity_id)))
        assert response.status_code == 404
