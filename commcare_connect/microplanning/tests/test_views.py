from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest import mock

import pytest
from django.test import Client
from django.urls import reverse

from commcare_connect.flags.flag_names import MICROPLANNING
from commcare_connect.flags.models import Flag
from commcare_connect.microplanning import views as microplanning_views


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
