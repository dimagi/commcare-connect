from __future__ import annotations

import csv as csv_mod
import io
import json
import uuid
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock
from unittest.mock import MagicMock, Mock, patch

import pytest
from django import forms
from django.contrib.gis.geos import Point, Polygon
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.db.utils import OperationalError
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from commcare_connect.flags.flag_names import MICROPLANNING
from commcare_connect.flags.models import Flag
from commcare_connect.microplanning import views as microplanning_views
from commcare_connect.microplanning.const import (
    NO_CHILDREN_WORK_AREA_UNIT_SLUG,
    SEARCH_KIND_FILTERS,
    SERVICE_DELIVERY_UNIT_SLUG,
)
from commcare_connect.microplanning.exceptions import BuildingDataUnavailable
from commcare_connect.microplanning.filters import WorkAreaMapFilterSet
from commcare_connect.microplanning.forms import AssignmentModeForm
from commcare_connect.microplanning.models import (
    SRID,
    ImplementationArea,
    InaccessibilityRequestStatus,
    WorkArea,
    WorkAreaGroup,
    WorkAreaStatus,
)
from commcare_connect.microplanning.tasks import WorkAreaCSVExporter, get_implementation_area_import_cache_key
from commcare_connect.microplanning.tests.factories import (
    ImplementationAreaFactory,
    WorkAreaFactory,
    WorkAreaGroupFactory,
    WorkAreaInaccessibilityRequestFactory,
)
from commcare_connect.microplanning.views import (
    MAX_EXCLUDE_WORK_AREAS,
    MAX_UNASSIGN_WORK_AREAS,
    InaccessibilityReviewAction,
    UserVisitVectorLayer,
    get_metrics_for_microplanning,
)
from commcare_connect.opportunity.models import BlobMeta, VisitValidationStatus
from commcare_connect.opportunity.tests.factories import (
    DeliverUnitFactory,
    OpportunityAccessFactory,
    OpportunityFactory,
    PaymentUnitFactory,
    UserVisitFactory,
)
from commcare_connect.utils.commcarehq_api import CommCareHQAPIException


def work_area_at(opportunity, x, y, **kwargs):
    """A 1x1 degree work area with its lower-left corner at (x, y).

    The factory gives every work area the same fixed boundary, so a filter could never be seen to
    narrow an extent; the bounds tests place each one deliberately instead.
    """
    return WorkAreaFactory(
        opportunity=opportunity,
        boundary=Polygon(((x, y), (x + 1, y), (x + 1, y + 1), (x, y + 1), (x, y)), srid=SRID),
        centroid=Point(x + 0.5, y + 0.5, srid=SRID),
        **kwargs,
    )


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

    def test_download_template_row_matches_headers(self, client, org_user_admin, opportunity):
        url = self.get_url(opportunity.organization.slug, opportunity.opportunity_id)
        client.force_login(org_user_admin)

        response = client.get(url)

        rows = list(csv_mod.reader(io.StringIO(response.content.decode())))
        header_row, sample_row = rows[0], rows[1]
        assert header_row[-1] == "Work Area Group Name"
        assert len(sample_row) == len(header_row)


@pytest.mark.django_db
class TestImplementationAreaUpload(BaseMicroplanningFlagTest):
    CSV_CONTENT = (
        b"Implementation Area Name,Centroid,Boundary\nWard North,77.1 28.6,POLYGON((77 28,78 28,78 29,77 29,77 28))\n"
    )

    @pytest.fixture
    def csv_file(self):
        return SimpleUploadedFile("ia.csv", self.CSV_CONTENT, content_type="text/csv")

    def get_url(self, org_slug, opp_id):
        return reverse(
            "microplanning:upload_implementation_areas",
            kwargs={"org_slug": org_slug, "opp_id": opp_id},
        )

    @patch("commcare_connect.microplanning.views.import_implementation_areas_task.delay")
    def test_locking_and_redirect(self, mock_delay, client, org_user_admin, organization, opportunity, csv_file):
        mock_delay.return_value = Mock(id="00000000-0000-0000-0000-000000000000")
        client.force_login(org_user_admin)
        url = self.get_url(organization.slug, opportunity.opportunity_id)
        response = client.post(url, {"csv_file": csv_file})
        assert response.status_code == 302
        assert "task_id=" in response.url
        assert "area_type=implementation_area" in response.url
        mock_delay.assert_called_once()
        assert cache.get(get_implementation_area_import_cache_key(opportunity.id)) is not None

    def test_template_download(self, client, org_user_admin, organization, opportunity):
        client.force_login(org_user_admin)
        url = self.get_url(organization.slug, opportunity.opportunity_id)
        response = client.get(url)
        assert response.status_code == 200
        assert response["Content-Disposition"] == 'attachment; filename="implementation_area_template.csv"'
        assert b"Implementation Area Name" in response.content

    @pytest.mark.parametrize("setup_microplanning_flag", [False], indirect=True)
    def test_flag_required(self, client, org_user_admin, organization, opportunity):
        client.force_login(org_user_admin)
        url = self.get_url(organization.slug, opportunity.opportunity_id)
        assert client.get(url).status_code == 404

    def test_status_modal_renders_implementation_area_title(self, client, org_user_admin, organization, opportunity):
        client.force_login(org_user_admin)
        url = reverse(
            "microplanning:implementation_area_import_status",
            kwargs={"org_slug": organization.slug, "opp_id": opportunity.opportunity_id},
        )
        response = client.get(url)
        assert response.status_code == 200
        assert b"Upload Implementation Area" in response.content
        assert (
            reverse(
                "microplanning:upload_implementation_areas",
                kwargs={"org_slug": organization.slug, "opp_id": opportunity.opportunity_id},
            ).encode()
            in response.content
        )

    def test_clear_deletes_areas_and_keeps_work_area_names(self, client, org_user_admin, organization, opportunity):
        area = ImplementationAreaFactory(opportunity=opportunity, name="Ward North")
        work_area = WorkAreaFactory(
            opportunity=opportunity, implementation_area=area, implementation_area_name="Ward North"
        )
        client.force_login(org_user_admin)
        url = reverse(
            "microplanning:clear_implementation_areas",
            kwargs={"org_slug": organization.slug, "opp_id": opportunity.opportunity_id},
        )

        response = client.post(url)

        assert response.status_code == 200
        assert response["HX-Redirect"]
        assert not ImplementationArea.objects.filter(opportunity=opportunity).exists()
        work_area.refresh_from_db()
        assert work_area.implementation_area_id is None
        assert work_area.implementation_area_name == "Ward North"


@pytest.mark.django_db
class TestBuildingsGeojson(BaseMicroplanningFlagTest):
    @pytest.fixture(autouse=True)
    def isolated_cache(self, local_cache):
        """Grid tile lookups must not carry over between tests, or a fetch under test never happens."""

    def url(self, org_slug, opp_id):
        return reverse(
            "microplanning:buildings_geojson",
            kwargs={"org_slug": org_slug, "opp_id": opp_id},
        )

    def test_returns_feature_collection(self, client, org_user_admin, organization, opportunity):
        feature = {
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [[[8.65, 9.05], [8.66, 9.05], [8.66, 9.06], [8.65, 9.05]]]},
            "properties": {"id": "building-1"},
        }
        client.force_login(org_user_admin)
        with patch(
            "commcare_connect.microplanning.buildings.fetch_buildings_for_grid_tiles",
            side_effect=lambda grid_tiles: {
                grid_tiles[0]: [feature],
                **{grid_tile: [] for grid_tile in grid_tiles[1:]},
            },
        ):
            response = client.get(
                self.url(organization.slug, opportunity.opportunity_id), {"bbox": "8.65,9.05,8.70,9.09"}
            )

        assert response.status_code == 200
        body = response.json()
        assert body["type"] == "FeatureCollection"
        assert body["features"] == [feature]
        # The bbox reported back is the snapped area, so it always contains what was asked for.
        west, south, east, north = body["bbox"]
        assert west <= 8.65 and south <= 9.05 and east >= 8.70 and north >= 9.09

    @pytest.mark.parametrize(
        "bbox",
        [None, "", "8.65,9.05,8.70", "8.65,9.05,8.70,north", "8.70,9.05,8.65,9.09"],
    )
    def test_bad_bbox_is_rejected(self, client, org_user_admin, organization, opportunity, bbox):
        client.force_login(org_user_admin)
        params = {} if bbox is None else {"bbox": bbox}
        response = client.get(self.url(organization.slug, opportunity.opportunity_id), params)
        assert response.status_code == 400
        assert response.json()["error"]

    def test_too_large_an_area_is_reported_apart_from_a_malformed_one(
        self, client, org_user_admin, organization, opportunity
    ):
        """422, not the 400 a bad bbox gets: the map asks the user to zoom in only for this one."""
        client.force_login(org_user_admin)
        response = client.get(self.url(organization.slug, opportunity.opportunity_id), {"bbox": "3.0,4.0,14.0,14.0"})
        assert response.status_code == 422
        assert "too large" in response.json()["error"]

    def test_unavailable_upstream_is_reported_as_503(self, client, org_user_admin, organization, opportunity):
        client.force_login(org_user_admin)
        with patch(
            "commcare_connect.microplanning.buildings.fetch_buildings_for_grid_tiles",
            side_effect=BuildingDataUnavailable("overture is down"),
        ):
            response = client.get(
                self.url(organization.slug, opportunity.opportunity_id), {"bbox": "8.65,9.05,8.70,9.09"}
            )

        assert response.status_code == 503
        assert response.json()["error"]

    def test_overture_failing_to_open_is_reported_as_503(self, client, org_user_admin, organization, opportunity):
        """The package raises rather than returning None when it cannot resolve the release."""
        client.force_login(org_user_admin)
        with patch(
            "overturemaps.record_batch_reader",
            side_effect=Exception("Could not fetch STAC catalog: <urlopen error>"),
        ):
            response = client.get(
                self.url(organization.slug, opportunity.opportunity_id), {"bbox": "8.65,9.05,8.70,9.09"}
            )

        assert response.status_code == 503

    def test_a_bbox_too_narrow_to_cover_a_grid_tile_is_not_a_server_error(
        self, client, org_user_admin, organization, opportunity
    ):
        """parse_bbox admits this, so it must reach the fetch rather than blowing up on empty grid tiles."""
        client.force_login(org_user_admin)
        with patch(
            "commcare_connect.microplanning.buildings.fetch_buildings_for_grid_tiles",
            side_effect=lambda grid_tiles: {grid_tile: [] for grid_tile in grid_tiles},
        ):
            response = client.get(
                self.url(organization.slug, opportunity.opportunity_id),
                {"bbox": "0.0,0.0,0.0000000000001,0.0000000000001"},
            )

        assert response.status_code == 200

    def test_requires_org_admin(self, client, org_user_member, organization, opportunity):
        client.force_login(org_user_member)
        response = client.get(self.url(organization.slug, opportunity.opportunity_id), {"bbox": "8.65,9.05,8.70,9.09"})
        assert response.status_code == 404


@pytest.mark.django_db
class TestImplementationAreasGeojson(BaseMicroplanningFlagTest):
    def url(self, org_slug, opp_id):
        return reverse(
            "microplanning:implementation_areas_geojson",
            kwargs={"org_slug": org_slug, "opp_id": opp_id},
        )

    def test_returns_feature_per_area(self, client, org_user_admin, organization, opportunity):
        ImplementationAreaFactory(opportunity=opportunity, name="Ward North")
        client.force_login(org_user_admin)
        response = client.get(self.url(organization.slug, opportunity.opportunity_id))
        assert response.status_code == 200
        features = response.json()["implementation_area_features"]
        assert len(features) == 1
        feature = features[0]
        assert feature["type"] == "Feature"
        assert feature["properties"]["name"] == "Ward North"
        assert feature["geometry"]["type"] == "Polygon"
        assert feature["geometry"]["coordinates"]

    def test_empty_when_none(self, client, org_user_admin, organization, opportunity):
        client.force_login(org_user_admin)
        response = client.get(self.url(organization.slug, opportunity.opportunity_id))
        assert response.status_code == 200
        assert response.json() == {"implementation_area_features": []}

    def test_scoped_to_opportunity(self, client, org_user_admin, organization, opportunity):
        ImplementationAreaFactory(opportunity=opportunity, name="Mine")
        other_opp = OpportunityFactory(organization=organization)
        ImplementationAreaFactory(opportunity=other_opp, name="Theirs")
        client.force_login(org_user_admin)
        response = client.get(self.url(organization.slug, opportunity.opportunity_id))
        names = [f["properties"]["name"] for f in response.json()["implementation_area_features"]]
        assert names == ["Mine"]

    @pytest.mark.parametrize("setup_microplanning_flag", [False], indirect=True)
    def test_flag_required(self, client, org_user_admin, organization, opportunity):
        client.force_login(org_user_admin)
        assert client.get(self.url(organization.slug, opportunity.opportunity_id)).status_code == 404


@pytest.mark.django_db
class TestWorkAreasGroupGeojson(BaseMicroplanningFlagTest):
    def url(self, org_slug, opp_id):
        return reverse(
            "microplanning:workareas_group_geojson",
            kwargs={"org_slug": org_slug, "opp_id": opp_id},
        )

    def test_returns_a_feature_per_group(self, client, org_user_admin, organization, opportunity):
        group = WorkAreaGroupFactory(opportunity=opportunity)
        WorkAreaFactory(opportunity=opportunity, work_area_group=group)
        WorkAreaFactory(opportunity=opportunity, work_area_group=None)
        client.force_login(org_user_admin)

        payload = client.get(self.url(organization.slug, opportunity.opportunity_id)).json()

        assert [f["properties"]["group_id"] for f in payload["group_features"]] == [group.id]

    def test_serves_outlines_only(self, client, org_user_admin, organization, opportunity):
        """Bounds moved to workareas_bounds, which applies the map's filters; this view is outlines."""
        WorkAreaFactory(opportunity=opportunity)
        client.force_login(org_user_admin)

        payload = client.get(self.url(organization.slug, opportunity.opportunity_id)).json()

        assert set(payload) == {"group_features"}


@pytest.mark.django_db
class TestWorkAreaBoundsView(BaseMicroplanningFlagTest):
    """The map's auto-zoom source: the extent of whatever the current filters match."""

    def url(self, org_slug, opp_id):
        return reverse(
            "microplanning:workareas_bounds",
            kwargs={"org_slug": org_slug, "opp_id": opp_id},
        )

    def get_bounds(self, client, org_user_admin, organization, opportunity, params=None):
        client.force_login(org_user_admin)
        response = client.get(self.url(organization.slug, opportunity.opportunity_id), data=params or {})
        assert response.status_code == 200
        return response.json()["bounds"]

    def test_unfiltered_covers_every_work_area(self, client, org_user_admin, organization, opportunity):
        work_area_at(opportunity, 10, 20)
        work_area_at(opportunity, 40, 50)

        bounds = self.get_bounds(client, org_user_admin, organization, opportunity)

        assert bounds == [10, 20, 41, 51]

    def test_status_filter_narrows_the_extent(self, client, org_user_admin, organization, opportunity):
        work_area_at(opportunity, 10, 20, status=WorkAreaStatus.VISITED)
        work_area_at(opportunity, 40, 50, status=WorkAreaStatus.NOT_VISITED)

        bounds = self.get_bounds(client, org_user_admin, organization, opportunity, {"status": WorkAreaStatus.VISITED})

        assert bounds == [10, 20, 11, 21]

    def test_work_area_group_filter_narrows_the_extent(self, client, org_user_admin, organization, opportunity):
        group = WorkAreaGroupFactory(opportunity=opportunity)
        work_area_at(opportunity, 10, 20, work_area_group=group)
        work_area_at(opportunity, 40, 50)

        bounds = self.get_bounds(client, org_user_admin, organization, opportunity, {"work_area_group": group.id})

        assert bounds == [10, 20, 11, 21]

    def test_implementation_area_filter_narrows_the_extent(self, client, org_user_admin, organization, opportunity):
        implementation_area = ImplementationAreaFactory(opportunity=opportunity)
        work_area_at(opportunity, 10, 20, implementation_area=implementation_area)
        work_area_at(opportunity, 40, 50)

        bounds = self.get_bounds(
            client, org_user_admin, organization, opportunity, {"implementation_area": implementation_area.id}
        )

        assert bounds == [10, 20, 11, 21]

    def test_work_area_filter_returns_just_that_area(self, client, org_user_admin, organization, opportunity):
        """The search box's path: picking one work area sets the hidden `work_area` filter."""
        target = work_area_at(opportunity, 10, 20)
        work_area_at(opportunity, 40, 50)

        bounds = self.get_bounds(client, org_user_admin, organization, opportunity, {"work_area": target.id})

        assert bounds == [10, 20, 11, 21]

    def test_visit_date_filter_narrows_the_extent(self, client, org_user_admin, organization, opportunity):
        """The uservisit-joining, DISTINCT-ed case — duplicate rows must not disturb the extent."""
        access = OpportunityAccessFactory(opportunity=opportunity)
        in_range = work_area_at(opportunity, 10, 20)
        out_of_range = work_area_at(opportunity, 40, 50)
        # Two visits on the in-range area, so the join duplicates its row.
        for _ in range(2):
            UserVisitFactory(
                opportunity=opportunity,
                user=access.user,
                work_area=in_range,
                visit_date=datetime(2025, 6, 1, tzinfo=timezone.utc),
            )
        UserVisitFactory(
            opportunity=opportunity,
            user=access.user,
            work_area=out_of_range,
            visit_date=datetime(2025, 1, 1, tzinfo=timezone.utc),
        )

        bounds = self.get_bounds(
            client,
            org_user_admin,
            organization,
            opportunity,
            {"start_date": "2025-05-01", "end_date": "2025-07-01"},
        )

        assert bounds == [10, 20, 11, 21]

    def test_no_matches_returns_null(self, client, org_user_admin, organization, opportunity):
        work_area_at(opportunity, 10, 20, status=WorkAreaStatus.VISITED)

        bounds = self.get_bounds(
            client, org_user_admin, organization, opportunity, {"status": WorkAreaStatus.INACCESSIBLE}
        )

        assert bounds is None

    def test_no_work_areas_returns_null(self, client, org_user_admin, organization, opportunity):
        assert self.get_bounds(client, org_user_admin, organization, opportunity) is None

    def test_scoped_to_opportunity(self, client, org_user_admin, organization, opportunity):
        work_area_at(opportunity, 10, 20)
        other_opp = OpportunityFactory(organization=organization)
        work_area_at(other_opp, 40, 50)

        bounds = self.get_bounds(client, org_user_admin, organization, opportunity)

        assert bounds == [10, 20, 11, 21]

    @pytest.mark.parametrize("setup_microplanning_flag", [False], indirect=True)
    def test_flag_required(self, client, org_user_admin, organization, opportunity):
        client.force_login(org_user_admin)
        assert client.get(self.url(organization.slug, opportunity.opportunity_id)).status_code == 404


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

    @pytest.mark.parametrize(
        "create_deliver_units, expected_quoted",
        [
            pytest.param(False, '"services_delivery_unit", "no-children-wa"', id="both-missing"),
            pytest.param(True, "", id="both-present"),
        ],
    )
    def test_missing_deliver_units_banner(
        self,
        client: Client,
        settings,
        organization,
        org_user_admin,
        opportunity,
        create_deliver_units,
        expected_quoted,
    ):
        if create_deliver_units:
            DeliverUnitFactory(app=opportunity.deliver_app, slug=SERVICE_DELIVERY_UNIT_SLUG)
            DeliverUnitFactory(app=opportunity.deliver_app, slug=NO_CHILDREN_WORK_AREA_UNIT_SLUG)
        settings.MAPBOX_TOKEN = "test-mapbox-token"
        client.force_login(org_user_admin)
        response = client.get(self.url(organization.slug, str(opportunity.opportunity_id)))
        assert response.context["quoted_missing_deliver_units"] == expected_quoted
        assert (SERVICE_DELIVERY_UNIT_SLUG.encode() in response.content) == (not create_deliver_units)

    def test_sidebar_shows_implementation_area_label(
        self, client: Client, settings, organization, org_user_admin, opportunity
    ):
        # The "Select Work Area" sidebar detail panel labels the selected area's Implementation Area.
        settings.MAPBOX_TOKEN = "test-mapbox-token"
        client.force_login(org_user_admin)
        response = client.get(self.url(organization.slug, str(opportunity.opportunity_id)))
        assert b"Implementation Area" in response.content
        assert b"selectedFeature.implementation_area_name" in response.content

    def test_map_wires_implementation_area_hover(
        self, client: Client, settings, organization, org_user_admin, opportunity
    ):
        # The map registers a hover handler on the Implementation Area outline layer
        # (highlight + name tooltip).
        settings.MAPBOX_TOKEN = "test-mapbox-token"
        client.force_login(org_user_admin)
        response = client.get(self.url(organization.slug, str(opportunity.opportunity_id)))
        assert b"'mousemove', 'implementation-areas-outline'" in response.content
        assert b"implementationAreaPopup" in response.content

    def test_map_shows_toast_on_implementation_area_fetch_error(
        self, client: Client, settings, organization, org_user_admin, opportunity
    ):
        # A failed Implementation Areas fetch surfaces a visible toast, not just a console error.
        settings.MAPBOX_TOKEN = "test-mapbox-token"
        client.force_login(org_user_admin)
        response = client.get(self.url(organization.slug, str(opportunity.opportunity_id)))
        assert b"Failed to load Implementation Areas." in response.content

    def test_map_has_implementation_area_layer_toggle(
        self, client: Client, settings, organization, org_user_admin, opportunity
    ):
        # The filter sidebar exposes a switch to show/hide the Implementation Area layer.
        settings.MAPBOX_TOKEN = "test-mapbox-token"
        client.force_login(org_user_admin)
        response = client.get(self.url(organization.slug, str(opportunity.opportunity_id)))
        assert b"Show Implementation Areas" in response.content
        assert b"showImplementationAreas" in response.content

    @pytest.mark.parametrize("setup_microplanning_flag", [False], indirect=True)
    def test_flag_disabled(self, client: Client, organization, org_user_admin, opportunity):
        client.force_login(org_user_admin)
        response = client.get(self.url(organization.slug, str(opportunity.opportunity_id)))
        assert response.status_code == 404

    def test_unauthenticated(self, client: Client, organization, org_user_member, opportunity):
        client.force_login(org_user_member)
        response = client.get(self.url(organization.slug, str(opportunity.opportunity_id)))
        assert response.status_code == 404

    def test_upload_and_clear_buttons_toggle_on_implementation_areas(
        self, client: Client, settings, organization, org_user_admin, opportunity
    ):
        settings.MAPBOX_TOKEN = "test-mapbox-token"
        client.force_login(org_user_admin)
        url = self.url(organization.slug, str(opportunity.opportunity_id))

        response = client.get(url)
        assert response.context["show_implementation_area_btn"] is True
        assert response.context["implementation_areas_present"] is False
        assert b"Upload Implementation Area" in response.content
        # The Clear Data entry is always rendered; with nothing uploaded it is disabled and says so.
        assert b"Clear Implementation Areas" in response.content
        assert (
            response.context["clear_data_details"]["implementation_areas"]
            == "No Implementation Areas have been uploaded yet."
        )

        ImplementationAreaFactory(opportunity=opportunity)

        response = client.get(url)
        assert response.context["show_implementation_area_btn"] is False
        assert response.context["implementation_areas_present"] is True
        assert b"Upload Implementation Area" not in response.content
        assert b"Clear Implementation Areas" in response.content
        assert response.context["clear_data_details"]["implementation_areas"] == "1 record"

    def test_auto_poll_targets_matching_status_endpoint(
        self, client: Client, settings, organization, org_user_admin, opportunity
    ):
        settings.MAPBOX_TOKEN = "test-mapbox-token"
        client.force_login(org_user_admin)
        base = self.url(organization.slug, str(opportunity.opportunity_id))
        wa_status = reverse(
            "microplanning:import_status",
            kwargs={"org_slug": organization.slug, "opp_id": opportunity.opportunity_id},
        )
        ia_status = reverse(
            "microplanning:implementation_area_import_status",
            kwargs={"org_slug": organization.slug, "opp_id": opportunity.opportunity_id},
        )

        wa_response = client.get(f"{base}?task_id=abc")
        assert wa_response.context["import_status_url"] == wa_status

        ia_response = client.get(f"{base}?task_id=abc&area_type=implementation_area")
        assert ia_response.context["import_status_url"] == ia_status
        assert ia_status.encode() in ia_response.content

    @mock.patch("commcare_connect.microplanning.views.AsyncResult")
    def test_import_status_renders_errors_as_table(
        self, mock_async_result, client: Client, organization, org_user_admin, opportunity
    ):
        task = mock_async_result.return_value
        task.ready.return_value = True
        task.successful.return_value = True
        task.result = {"errors": {"Centroid must be in 'lon lat' format": [4, 17]}}
        client.force_login(org_user_admin)
        url = reverse(
            "microplanning:import_status",
            kwargs={"org_slug": organization.slug, "opp_id": opportunity.opportunity_id},
        )

        response = client.get(url, {"task_id": str(uuid.uuid4())})

        content = response.content.decode()
        assert "Centroid must be in &#x27;lon lat&#x27; format" in content  # autoescaped, not marked safe
        assert "4, 17" in content
        assert "Error Description" in content

    def test_rerun_and_clear_buttons_shown_post_clustering(self, client, org_user_admin, opportunity):
        """After clustering (groups exist) with no FLW assignments, rerun/clear controls are offered."""
        group = WorkAreaGroupFactory(opportunity=opportunity)
        WorkAreaFactory(opportunity=opportunity, work_area_group=group)
        client.force_login(org_user_admin)

        response = client.get(self.url(opportunity.organization.slug, str(opportunity.opportunity_id)))

        assert response.context["show_rerun_clear_work_area_groups_btn"] is True
        assert response.context["clustering_is_rerun"] is True
        # The one-time "create" control is gone once groups exist.
        assert response.context["show_workarea_groups_btn"] is False

    def test_rerun_and_clear_buttons_hidden_when_assigned(self, client, org_user_admin, opportunity):
        """Once any work area is assigned to an FLW, rerun/clear controls disappear."""
        group = WorkAreaGroupFactory(opportunity=opportunity)
        access = OpportunityAccessFactory(opportunity=opportunity)
        WorkAreaFactory(opportunity=opportunity, work_area_group=group, opportunity_access=access)
        client.force_login(org_user_admin)

        response = client.get(self.url(opportunity.organization.slug, str(opportunity.opportunity_id)))

        assert response.context["show_rerun_clear_work_area_groups_btn"] is False
        assert response.context["clustering_is_rerun"] is False

    def test_rerun_and_clear_buttons_hidden_before_clustering(self, client, org_user_admin, opportunity):
        """Before any clustering has run, only the initial create control is available."""
        WorkAreaFactory(opportunity=opportunity)
        client.force_login(org_user_admin)

        response = client.get(self.url(opportunity.organization.slug, str(opportunity.opportunity_id)))

        assert response.context["show_rerun_clear_work_area_groups_btn"] is False
        assert response.context["show_workarea_groups_btn"] is True

    @pytest.mark.parametrize(
        ("has_work_area", "assigned", "expected"),
        [
            pytest.param(True, False, True, id="unassigned-areas-present"),
            pytest.param(True, True, False, id="areas-assigned"),
            pytest.param(False, False, False, id="no-areas"),
        ],
    )
    def test_clear_work_areas_button_visibility(
        self, client, org_user_admin, opportunity, has_work_area, assigned, expected
    ):
        if has_work_area:
            access = OpportunityAccessFactory(opportunity=opportunity) if assigned else None
            WorkAreaFactory(opportunity=opportunity, opportunity_access=access)
        client.force_login(org_user_admin)

        response = client.get(self.url(opportunity.organization.slug, str(opportunity.opportunity_id)))

        assert response.context["show_clear_work_areas_btn"] is expected


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
        assert group.centroid is None
        group.refresh_from_db()
        assert group.centroid.x == 77.5
        assert group.centroid.y == 28.5

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

    @patch("commcare_connect.microplanning.views.create_or_update_case_by_work_area")
    def test_successful_group_field_update_for_centroid_recalculation(
        self, mock_sync, client, org_user_admin, opportunity
    ):
        group_1 = WorkAreaGroupFactory(opportunity=opportunity, name="group_1")
        access = OpportunityAccessFactory(opportunity=opportunity)
        work_area = WorkAreaFactory(
            opportunity=opportunity,
            expected_visit_count=10,
            opportunity_access=access,
            work_area_group=group_1,
        )
        work_area.work_area_group.update_centroid()

        initial_event_count = (
            work_area.expected_visit_count_work_area_group_status_opportunity_access_excluded_reason_events.count()
        )
        assert work_area.work_area_group

        group_2 = WorkAreaGroupFactory(opportunity=opportunity, name="group_2")

        new_expected_visit_count = 25
        client.force_login(org_user_admin)
        response = client.post(
            self.url(opportunity.organization.slug, str(opportunity.opportunity_id), work_area.id),
            {
                "expected_visit_count": new_expected_visit_count,
                "work_area_group": group_2.id,
                "reason": "Boundary adjusted",
            },
        )
        assert response.status_code == 204
        trigger = json.loads(response["HX-Trigger"])
        assert "workAreaUpdated" in trigger
        assert trigger["workAreaUpdated"]["expected_visit_count"] == new_expected_visit_count
        assert trigger["workAreaUpdated"]["group_id"] == group_2.id
        assert trigger["workAreaUpdated"]["group_name"] == group_2.name
        assert mock_sync.call_count == 1

        work_area.refresh_from_db()
        assert work_area.expected_visit_count == new_expected_visit_count
        assert work_area.work_area_group == group_2

        events = work_area.expected_visit_count_work_area_group_status_opportunity_access_excluded_reason_events
        assert events.count() == initial_event_count + 1
        event = events.last()
        assert event.pgh_context.metadata["reason"] == "Boundary adjusted"
        assert event.expected_visit_count == new_expected_visit_count
        assert event.work_area_group == group_2

        assert group_1.centroid.x == 77.5
        assert group_1.centroid.y == 28.5
        group_1.refresh_from_db()
        assert group_1.centroid is None

        assert group_2.centroid is None
        group_2.refresh_from_db()
        assert group_2.centroid.x == 77.5
        assert group_2.centroid.y == 28.5


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

    def test_implementation_area_name_is_a_tile_field(self):
        # Exposed in the vector tile so the map sidebar can show it for the selected work area.
        assert "implementation_area_name" in microplanning_views.WorkAreaVectorLayer.tile_fields

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
        assert row.assignee_phone == access.user.phone_number


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

    def test_search_kinds_map_to_real_filters(self):
        """Each search kind must name a filter this filter set actually declares.

        The map JS applies the ``filter_name`` stamped on each search option verbatim, so a filter
        renamed here without updating SEARCH_KIND_FILTERS would silently stop filtering the map
        rather than raise anywhere.
        """
        assert set(SEARCH_KIND_FILTERS.values()) <= set(WorkAreaMapFilterSet.base_filters)

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

    def test_implementation_area_filter(self, opportunity, work_areas):
        impl_area = ImplementationAreaFactory(opportunity=opportunity)
        work_areas.wa_visited.implementation_area = impl_area
        work_areas.wa_visited.save(update_fields=["implementation_area"])

        result = self._filter_ids({"implementation_area": [impl_area.id]}, opportunity)
        assert result == {work_areas.wa_visited.id}

    def test_work_area_group_filter(self, opportunity, work_areas):
        other_group = WorkAreaGroupFactory(opportunity=opportunity)
        wa_other_group = WorkAreaFactory(opportunity=opportunity, work_area_group=other_group)

        result = self._filter_ids({"work_area_group": [work_areas.group.id]}, opportunity)
        assert result == {work_areas.wa_not_visited.id, work_areas.wa_visited.id}
        assert wa_other_group.id not in result

    def test_payment_unit_filter(self, opportunity, work_areas):
        payment_unit = PaymentUnitFactory(opportunity=opportunity)
        deliver_unit = DeliverUnitFactory(payment_unit=payment_unit)
        UserVisitFactory(
            opportunity=opportunity,
            user=work_areas.access.user,
            work_area=work_areas.wa_visited,
            deliver_unit=deliver_unit,
        )

        result = self._filter_ids({"payment_unit": payment_unit.id}, opportunity)
        assert result == {work_areas.wa_visited.id}

    def test_unassigned_only_is_a_checkbox_with_no_unknown_option(self, opportunity):
        fs = WorkAreaMapFilterSet({}, queryset=WorkArea.objects.none(), opportunity=opportunity)
        widget = fs.form.fields["unassigned_only"].widget
        assert isinstance(widget, forms.CheckboxInput)

    def test_unassigned_only_still_filters_when_true(self, opportunity, work_areas):
        result = self._filter_ids({"unassigned_only": "True"}, opportunity)
        assert result == {work_areas.wa_unassigned.id}

    def test_work_area_filter(self, opportunity, work_areas):
        """Picking a work area in the search box narrows to just that one. Groups and
        implementation areas reuse the sidebar's own filters, so they need no equivalent here."""
        result = self._filter_ids({"work_area": work_areas.wa_visited.id}, opportunity)
        assert result == {work_areas.wa_visited.id}

    def test_work_area_filter_ignores_empty_value(self, opportunity, work_areas):
        """The hidden input is submitted on every filter change, so a blank value has to be a
        no-op rather than an error that empties the map."""
        expected = {work_areas.wa_not_visited.id, work_areas.wa_visited.id, work_areas.wa_unassigned.id}
        assert self._filter_ids({"work_area": ""}, opportunity) == expected

    def test_work_area_filter_matches_nothing_for_another_opportunitys_area(self, opportunity, work_areas):
        other_work_area = WorkAreaFactory()

        assert self._filter_ids({"work_area": other_work_area.id}, opportunity) == set()

    def test_work_area_filter_combines_with_status(self, opportunity, work_areas):
        result = self._filter_ids(
            {"work_area": work_areas.wa_visited.id, "status": [WorkAreaStatus.NOT_VISITED]}, opportunity
        )
        assert result == set()


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
            "",
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
        assert row[6:] == ["0", "", "", "", ""]

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

    @patch("commcare_connect.microplanning.helpers.bulk_create_or_update_cases_by_work_areas")
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
        "action, expected_status, expected_request_status, expect_notify",
        [
            ("approve", WorkAreaStatus.INACCESSIBLE, InaccessibilityRequestStatus.APPROVED, False),
            ("deny", WorkAreaStatus.NOT_VISITED, InaccessibilityRequestStatus.DENIED, True),
        ],
        ids=["approve", "deny"],
    )
    def test_action_transitions_status(
        self,
        action,
        expected_status,
        expected_request_status,
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

        old_wag_centroid_value = work_area.work_area_group.centroid

        with (
            patch("commcare_connect.microplanning.views.send_push_notification_task") as mock_notif,
            patch("commcare_connect.microplanning.views.create_or_update_case_by_work_area"),
            django_capture_on_commit_callbacks(execute=True),
        ):
            response = client.post(url, {"action": action})

        assert response.status_code == 204
        work_area.refresh_from_db()
        assert work_area.status == expected_status
        inacc_request.refresh_from_db()
        assert inacc_request.status == expected_request_status
        hx_trigger = json.loads(response["HX-Trigger"])
        assert "inaccessibilityReviewed" in hx_trigger
        assert hx_trigger["inaccessibilityReviewed"]["status"] == expected_status

        event = work_area.expected_visit_count_work_area_group_status_opportunity_access_excluded_reason_events.last()
        assert event.pgh_context.metadata["username"] == org_user_admin.username
        assert event.pgh_context.metadata["user_email"] == org_user_admin.email

        if action == InaccessibilityReviewAction.APPROVE.value:
            assert work_area.work_area_group.centroid is None

        if action == InaccessibilityReviewAction.DENY.value:
            assert work_area.work_area_group.centroid == old_wag_centroid_value

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

    def test_review_modal_returns_pending_not_historical_request(
        self, client, org_user_admin, pending_wa, organization
    ):
        work_area, historical_request = pending_wa
        historical_request.status = InaccessibilityRequestStatus.DENIED
        historical_request.save(update_fields=["status"])
        pending_request = WorkAreaInaccessibilityRequestFactory(
            work_area=work_area,
            opportunity_access=historical_request.opportunity_access,
            status=InaccessibilityRequestStatus.PENDING,
        )
        client.force_login(org_user_admin)
        url = self.get_url(organization.slug, work_area.opportunity.opportunity_id, work_area.id)
        response = client.get(url)
        assert response.status_code == 200
        assert response.context["inaccessibility_request"].id == pending_request.id

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
class TestAssignmentModeContext:
    @pytest.fixture(autouse=True)
    def setup_flag(self, managed_opportunity):
        flag, _ = Flag.objects.get_or_create(name=MICROPLANNING)
        flag.opportunities.add(managed_opportunity)
        flag.flush()

    def url(self, org_slug, opp_id):
        return reverse("microplanning:microplanning_home", args=(org_slug, opp_id))

    def test_worker_list_url_points_to_work_area_assignments_tab(
        self, client, settings, program_manager_org, program_manager_org_user_admin, managed_opportunity
    ):
        settings.MAPBOX_TOKEN = "test-mapbox-token"
        client.force_login(program_manager_org_user_admin)

        response = client.get(
            self.url(program_manager_org.slug, str(managed_opportunity.opportunity_id)),
            {"assignment_mode": "1"},
        )

        assert response.status_code == 200
        expected_url = reverse(
            "opportunity:worker_work_areas",
            args=(program_manager_org.slug, managed_opportunity.opportunity_id),
        )
        assert response.context["worker_list_url"] == expected_url

    def test_work_area_group_field_accepts_multiple_groups(self, managed_opportunity):
        group_a = WorkAreaGroupFactory(opportunity=managed_opportunity)
        group_b = WorkAreaGroupFactory(opportunity=managed_opportunity)

        form = AssignmentModeForm(
            data={"work_area_group": [group_a.id, group_b.id]},
            opportunity=managed_opportunity,
        )

        assert form.is_valid()
        assert set(form.cleaned_data["work_area_group"]) == {group_a, group_b}


class TestGetWorkAreasForAssignment:
    @pytest.fixture(autouse=True)
    def setup_flag(self, managed_opportunity):
        flag, _ = Flag.objects.get_or_create(name=MICROPLANNING)
        flag.opportunities.add(managed_opportunity)
        flag.flush()

    def url(self, org_slug, opp_id):
        return reverse(
            "microplanning:get_work_areas_for_assignment",
            kwargs={"org_slug": org_slug, "opp_id": opp_id},
        )

    def test_returns_union_of_multiple_groups(
        self, client, program_manager_org, program_manager_org_user_admin, managed_opportunity
    ):
        group_a = WorkAreaGroupFactory(opportunity=managed_opportunity)
        group_b = WorkAreaGroupFactory(opportunity=managed_opportunity)
        other_group = WorkAreaGroupFactory(opportunity=managed_opportunity)
        wa_a = WorkAreaFactory(opportunity=managed_opportunity, work_area_group=group_a)
        wa_b = WorkAreaFactory(opportunity=managed_opportunity, work_area_group=group_b)
        WorkAreaFactory(opportunity=managed_opportunity, work_area_group=other_group)
        client.force_login(program_manager_org_user_admin)

        response = client.get(
            self.url(program_manager_org.slug, managed_opportunity.opportunity_id),
            {"group_id": [group_a.id, group_b.id]},
        )

        assert response.status_code == 200
        work_areas = response.json()["work_areas"]
        returned_ids = {wa["id"] for wa in work_areas}
        assert returned_ids == {wa_a.id, wa_b.id}
        group_id_by_wa_id = {wa["id"]: wa["group_id"] for wa in work_areas}
        assert group_id_by_wa_id == {wa_a.id: group_a.id, wa_b.id: group_b.id}

    def test_no_group_ids_returns_empty(
        self, client, program_manager_org, program_manager_org_user_admin, managed_opportunity
    ):
        WorkAreaFactory(opportunity=managed_opportunity)
        client.force_login(program_manager_org_user_admin)

        response = client.get(self.url(program_manager_org.slug, managed_opportunity.opportunity_id))

        assert response.status_code == 200
        assert response.json()["work_areas"] == []
        assert response.json()["bounds"] is None

    def test_returns_bounds_of_the_selected_groups(
        self, client, program_manager_org, program_manager_org_user_admin, managed_opportunity
    ):
        """Assignment mode zooms from this, since these work areas bypass the tile filters."""
        group = WorkAreaGroupFactory(opportunity=managed_opportunity)
        work_area_at(managed_opportunity, 10, 20, work_area_group=group)
        work_area_at(managed_opportunity, 12, 22, work_area_group=group)
        work_area_at(managed_opportunity, 40, 50)  # another group, must not widen the box
        client.force_login(program_manager_org_user_admin)

        response = client.get(
            self.url(program_manager_org.slug, managed_opportunity.opportunity_id),
            {"group_id": [group.id]},
        )

        assert response.json()["bounds"] == [10, 20, 13, 23]


@pytest.mark.django_db
class TestGetFlwWorkAreasForAssignment:
    @pytest.fixture(autouse=True)
    def setup_flag(self, managed_opportunity):
        flag, _ = Flag.objects.get_or_create(name=MICROPLANNING)
        flag.opportunities.add(managed_opportunity)
        flag.flush()

    def url(self, org_slug, opp_id, assignee_id):
        return reverse(
            "microplanning:get_flw_work_areas_for_assignment",
            kwargs={"org_slug": org_slug, "opp_id": opp_id, "assignee_id": assignee_id},
        )

    def test_returns_the_assignees_areas_and_their_bounds(
        self, client, program_manager_org, program_manager_org_user_admin, managed_opportunity
    ):
        access = OpportunityAccessFactory(opportunity=managed_opportunity)
        mine = work_area_at(managed_opportunity, 10, 20, opportunity_access=access)
        work_area_at(managed_opportunity, 40, 50)  # unassigned, must not widen the box
        client.force_login(program_manager_org_user_admin)

        payload = client.get(self.url(program_manager_org.slug, managed_opportunity.opportunity_id, access.id)).json()

        assert [wa["id"] for wa in payload["work_areas"]] == [mine.id]
        assert payload["bounds"] == [10, 20, 11, 21]

    def test_no_areas_returns_null_bounds(
        self, client, program_manager_org, program_manager_org_user_admin, managed_opportunity
    ):
        access = OpportunityAccessFactory(opportunity=managed_opportunity)
        client.force_login(program_manager_org_user_admin)

        payload = client.get(self.url(program_manager_org.slug, managed_opportunity.opportunity_id, access.id)).json()

        assert payload == {"work_areas": [], "bounds": None}


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

    @patch("commcare_connect.microplanning.helpers.bulk_create_or_update_cases_by_work_areas")
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

    @patch("commcare_connect.microplanning.helpers.bulk_create_or_update_cases_by_work_areas")
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

    def _make_work_areas(self, opp, statuses, expected_visit_counts=None, assigned=True):
        access = OpportunityAccessFactory(opportunity=opp) if assigned else None
        areas = []
        for i, status in enumerate(statuses):
            evc = expected_visit_counts[i] if expected_visit_counts else 10
            areas.append(
                WorkAreaFactory(opportunity=opp, status=status, expected_visit_count=evc, opportunity_access=access)
            )
        return areas

    def _make_visits(self, opp, work_area, *, approved=0, pending=0, deliver_unit=None):
        """Create `approved` approved + `pending` pending UserVisits for `work_area`."""
        deliver_unit = deliver_unit or DeliverUnitFactory(
            app=opp.deliver_app, slug=SERVICE_DELIVERY_UNIT_SLUG, name="Health Service Delivery"
        )
        for _ in range(approved):
            UserVisitFactory(
                opportunity=opp,
                work_area=work_area,
                deliver_unit=deliver_unit,
                status=VisitValidationStatus.approved,
            )
        for _ in range(pending):
            UserVisitFactory(
                opportunity=opp,
                work_area=work_area,
                deliver_unit=deliver_unit,
                status=VisitValidationStatus.pending,
            )

    def _get_metric(self, metrics, name):
        result = next((m for m in metrics if m["name"] == name), None)
        assert result is not None, f"Metric '{name}' not found in {[m['name'] for m in metrics]}"
        return result

    def test_unvisited_count_and_percentage(self, opp):
        """Unvisited = WAs still carrying the NOT_VISITED status, among in-scope areas."""
        _wa_not_visited, wa_visited, _wa_inaccessible, _wa_excluded = self._make_work_areas(
            opp,
            [
                WorkAreaStatus.NOT_VISITED,
                WorkAreaStatus.VISITED,
                WorkAreaStatus.INACCESSIBLE,
                WorkAreaStatus.EXCLUDED,
            ],
        )
        self._make_visits(opp, wa_visited, approved=2)

        metrics = get_metrics_for_microplanning(opp)
        m = self._get_metric(metrics, "Unvisited Work Areas")
        # in scope = 3 (wa_not_visited, wa_visited, wa_inaccessible); unvisited = 1
        assert m["value"] == 1
        assert m["percentage"] == 33  # round(1/3 * 100)

    def test_visited_children_found_count_and_percentage(self, opp):
        """Visited (children found) = WAs with >=1 approved HSD visit, among in-scope areas."""
        wa_visited_1, wa_visited_2, wa_no_visits, wa_pending_only, wa_excluded = self._make_work_areas(
            opp,
            [
                WorkAreaStatus.VISITED,
                WorkAreaStatus.VISITED,
                WorkAreaStatus.NOT_VISITED,
                WorkAreaStatus.VISITED,
                WorkAreaStatus.EXCLUDED,
            ],
        )
        self._make_visits(opp, wa_visited_1, approved=1)
        self._make_visits(opp, wa_visited_2, approved=3)
        self._make_visits(opp, wa_pending_only, pending=2)
        # Approved visits on an excluded WA must not bump the count.
        self._make_visits(opp, wa_excluded, approved=5)

        metrics = get_metrics_for_microplanning(opp)
        m = self._get_metric(metrics, "Visited Work Areas (children found)")
        # in scope = 4; visited = 2
        assert m["value"] == 2
        assert m["percentage"] == 50  # round(2/4 * 100)

    def test_visited_children_found_ignores_non_hsd_deliver_units(self, opp):
        """Only approved visits on the Service Delivery deliver unit count as 'children found'."""
        (wa,) = self._make_work_areas(opp, [WorkAreaStatus.VISITED])
        other_unit = DeliverUnitFactory(app=opp.deliver_app, slug="registration")
        self._make_visits(opp, wa, approved=1, deliver_unit=other_unit)

        metrics = get_metrics_for_microplanning(opp)
        m = self._get_metric(metrics, "Visited Work Areas (children found)")
        assert m["value"] == 0

    def test_visited_no_children_found_count_and_percentage(self, opp):
        """Visited (no children found) = WAs with >=1 approved No Children Work Area visit."""
        wa_ncwa, wa_hsd_only, wa_none, wa_excluded = self._make_work_areas(
            opp,
            [
                WorkAreaStatus.VISITED,
                WorkAreaStatus.VISITED,
                WorkAreaStatus.NOT_VISITED,
                WorkAreaStatus.EXCLUDED,
            ],
        )
        ncwa_unit = DeliverUnitFactory(app=opp.deliver_app, slug=NO_CHILDREN_WORK_AREA_UNIT_SLUG)
        self._make_visits(opp, wa_ncwa, approved=1, deliver_unit=ncwa_unit)
        self._make_visits(opp, wa_hsd_only, approved=1)  # HSD visit doesn't count toward this tile
        self._make_visits(opp, wa_excluded, approved=2, deliver_unit=ncwa_unit)

        metrics = get_metrics_for_microplanning(opp)
        m = self._get_metric(metrics, "Visited Work Areas (no children found)")
        # in scope = 3; no-children-found = 1 (wa_ncwa only)
        assert m["value"] == 1
        assert m["percentage"] == 33  # round(1/3 * 100)

    def test_evc_reached_count_and_percentage(self, opp):
        """EVC reached = WAs with approved HSD visits >= expected_visit_count, among in-scope areas.
        A WA with no expected count set has no target to reach, so it never counts.
        """
        wa_reached, wa_partial, wa_over, wa_no_target, wa_excluded_reached = self._make_work_areas(
            opp,
            [
                WorkAreaStatus.EXPECTED_VISIT_REACHED,
                WorkAreaStatus.VISITED,
                WorkAreaStatus.EXPECTED_VISIT_REACHED,
                WorkAreaStatus.NOT_VISITED,
                WorkAreaStatus.EXCLUDED,
            ],
            expected_visit_counts=[5, 5, 5, 0, 5],
        )
        self._make_visits(opp, wa_reached, approved=5)  # reached
        self._make_visits(opp, wa_partial, approved=4)  # not reached
        self._make_visits(opp, wa_over, approved=7)  # reached (>=)
        self._make_visits(opp, wa_excluded_reached, approved=10)  # excluded — ignored

        metrics = get_metrics_for_microplanning(opp)
        m = self._get_metric(metrics, "EVC Reached")
        # in scope = 4; reached = 2 - wa_no_target is not one of them
        assert m["value"] == 2
        assert m["percentage"] == 50  # round(2/4 * 100)

    def test_inaccessible_count_and_percentage(self, opp):
        """Inaccessible = INACCESSIBLE or REQUEST_FOR_INACCESSIBLE, among in-scope areas."""
        self._make_work_areas(
            opp,
            [
                WorkAreaStatus.INACCESSIBLE,
                WorkAreaStatus.REQUEST_FOR_INACCESSIBLE,
                WorkAreaStatus.VISITED,
                WorkAreaStatus.EXCLUDED,
            ],
        )
        metrics = get_metrics_for_microplanning(opp)
        m = self._get_metric(metrics, "Inaccessible Work Areas")
        # in scope = 3; inaccessible = 2
        assert m["value"] == 2
        assert m["percentage"] == 67  # round(2/3 * 100)

    def test_excluded_is_a_plain_count(self, opp):
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
        assert "percentage" not in m

    def test_work_areas_done_counts_each_area_once(self, opp):
        """Work Areas Done is the union of three: approved visit(HSD or NCWA ) or inaccessible."""
        # wa_overlap holds an approved HSD visit *and* is inaccessible, so it must count once, not
        # twice - otherwise the tile could exceed count(WA).
        wa_hsd, wa_ncwa, wa_inaccessible, wa_request_inaccessible, wa_overlap, wa_untouched, wa_excluded = (
            self._make_work_areas(
                opp,
                [
                    WorkAreaStatus.VISITED,
                    WorkAreaStatus.VISITED,
                    WorkAreaStatus.INACCESSIBLE,
                    WorkAreaStatus.REQUEST_FOR_INACCESSIBLE,
                    WorkAreaStatus.INACCESSIBLE,
                    WorkAreaStatus.NOT_VISITED,
                    WorkAreaStatus.EXCLUDED,
                ],
            )
        )
        self._make_visits(opp, wa_hsd, approved=1)
        ncwa_unit = DeliverUnitFactory(app=opp.deliver_app, slug=NO_CHILDREN_WORK_AREA_UNIT_SLUG)
        self._make_visits(opp, wa_ncwa, approved=1, deliver_unit=ncwa_unit)
        self._make_visits(opp, wa_overlap, approved=1)  # inaccessible *and* has an approved HSD visit
        # An excluded WA's approved visit must not count - it's out of scope entirely.
        self._make_visits(opp, wa_excluded, approved=1)

        metrics = get_metrics_for_microplanning(opp)
        m = self._get_metric(metrics, "Work Areas Done")
        # in scope = 6 (all but wa_excluded); done = 5 (wa_untouched is the only one left out)
        assert m["value"] == 5
        assert m["percentage"] == 83  # round(5/6 * 100)

    def test_ratio_ignores_non_hsd_deliver_units(self, opp):
        """Approved visits on any deliver unit other than Service Delivery must not feed the ratio."""
        (wa,) = self._make_work_areas(opp, [WorkAreaStatus.VISITED], expected_visit_counts=[10])
        other_unit = DeliverUnitFactory(app=opp.deliver_app, slug="registration")
        self._make_visits(opp, wa, approved=5, deliver_unit=other_unit)

        metrics = get_metrics_for_microplanning(opp)
        m = self._get_metric(metrics, "WA Visited : Visits Ratio")
        assert m["value"] == "--"  # zero HSD visits, as denominator pct_visits is zero

    def test_pct_visited_to_pct_visits(self, opp):
        """Ratio uses approved HSD visits on in-scope WAs only, and the HSD-based visited count.

        Setup:
          - wa_visited (NOT_VISITED, expected=10): 1 approved HSD visit -> counted as visited
          - wa_unvisited (NOT_VISITED, expected=10): 0 approved -> not visited
          - wa_excluded (EXCLUDED, expected=10): 3 approved -> excluded from both numerator and denominator
          pct_wa_visited = 1/2 = 0.5  (in scope = 2)
          total_hsd (in scope) = 1
          total_expected (in scope) = 10 + 10 = 20
          pct_visits = 1/20 = 0.05
          ratio = 0.5 / 0.05 = 10.0
        """
        wa_visited, wa_unvisited, wa_excluded = self._make_work_areas(
            opp,
            [WorkAreaStatus.NOT_VISITED, WorkAreaStatus.NOT_VISITED, WorkAreaStatus.EXCLUDED],
            expected_visit_counts=[10, 10, 10],
        )
        self._make_visits(opp, wa_visited, approved=1)
        # Approved visits on excluded WAs must be ignored.
        self._make_visits(opp, wa_excluded, approved=3)
        # Non-approved noise must be ignored.
        self._make_visits(opp, wa_unvisited, pending=5)
        UserVisitFactory(opportunity=opp, work_area=None, status=VisitValidationStatus.pending)

        metrics = get_metrics_for_microplanning(opp)
        m = self._get_metric(metrics, "WA Visited : Visits Ratio")
        assert m["value"] == 10.0
        assert "percentage" not in m
        assert "unit" not in m

    def test_pct_visited_to_pct_visits_zero_denominator(self, opp):
        """No visits and no expected visits → show '--'."""

        metrics = get_metrics_for_microplanning(opp)
        m = self._get_metric(metrics, "WA Visited : Visits Ratio")
        assert m["value"] == "--"
        assert "unit" not in m

    def test_pct_visited_ignores_visits_without_work_area(self, opp):
        """Approved visits with no work_area must not inflate the ratio's total_hsd denominator."""
        wa_visited, wa_unvisited = self._make_work_areas(
            opp,
            [WorkAreaStatus.VISITED, WorkAreaStatus.NOT_VISITED],
            expected_visit_counts=[10, 10],
        )
        self._make_visits(opp, wa_visited, approved=1)
        # Orphan approved visits (no work area) must be ignored.
        hsd_unit = DeliverUnitFactory(app=opp.deliver_app, slug=SERVICE_DELIVERY_UNIT_SLUG)
        for _ in range(3):
            UserVisitFactory(
                opportunity=opp, work_area=None, deliver_unit=hsd_unit, status=VisitValidationStatus.approved
            )

        metrics = get_metrics_for_microplanning(opp)
        m = self._get_metric(metrics, "WA Visited : Visits Ratio")
        # total_hsd (WA-attached) = 1 -> pct_visits = 1/20 = 0.05
        # pct_wa_visited = 1/2 = 0.5 -> ratio = 0.5 / 0.05 = 10.0
        assert m["value"] == 10.0

    def test_zero_in_scope_work_areas(self, opp):
        """All WAs excluded → percentage metrics show None; visited count is 0."""
        self._make_work_areas(opp, [WorkAreaStatus.EXCLUDED])
        metrics = get_metrics_for_microplanning(opp)
        m = self._get_metric(metrics, "Visited Work Areas (children found)")
        assert m["value"] == 0
        assert m["percentage"] is None

    def test_unassigned_work_areas_leave_every_total(self, opp):
        """An unassigned WA has no FLW to do the work, so it drops out of every numerator and denominator."""
        # in scope
        _, visited_wa = self._make_work_areas(
            opp, statuses=[WorkAreaStatus.NOT_VISITED, WorkAreaStatus.VISITED], expected_visit_counts=[5, 2]
        )
        self._make_visits(opp, visited_wa, approved=2)  # EVC reached, and "done", for this one
        # out of scope: excluded from every metric's denominator and numerator as unassigned
        wa_unassigned, _wa_inaccessible, _wa_excluded = self._make_work_areas(
            opp,
            [WorkAreaStatus.UNASSIGNED, WorkAreaStatus.INACCESSIBLE, WorkAreaStatus.EXCLUDED],
            expected_visit_counts=[1, 10, 10],
            assigned=False,
        )
        self._make_visits(opp, wa_unassigned, approved=1)

        tiles = {m["name"]: (m["value"], m.get("percentage")) for m in get_metrics_for_microplanning(opp)}
        assert tiles["Work Areas Done"] == (1, 50)  # denominator is 2, not 5
        assert tiles["Unvisited Work Areas"] == (1, 50)
        assert tiles["Visited Work Areas (children found)"] == (1, 50)
        assert tiles["EVC Reached"] == (1, 50)
        assert tiles["Inaccessible Work Areas"] == (0, 0)
        assert tiles["WA Visited : Visits Ratio"] == (1.75, None)
        # The one tile that must not follow the rule: its count is what the rule removes, over all areas.
        assert tiles["Excluded Work Areas"] == (1, None)


@pytest.mark.django_db
class TestCoverageProgressView(BaseMicroplanningFlagTest):
    def url(self, org_slug, opp_id):
        return reverse("microplanning:coverage_progress", args=(org_slug, opp_id))

    def test_renders_page_with_tables_in_context(self, client, org_user_admin, opportunity):
        WorkAreaFactory(
            opportunity=opportunity,
            opportunity_access=OpportunityAccessFactory(opportunity=opportunity),
            ward="w1",
            status=WorkAreaStatus.VISITED,
        )
        client.force_login(org_user_admin)
        resp = client.get(self.url(opportunity.organization.slug, str(opportunity.opportunity_id)))
        assert resp.status_code == 200
        assert "microplanning/coverage_progress.html" in {t.name for t in resp.templates}
        assert set(resp.context["header"].keys()) == {"ward_saturation_goal"}
        ward_table = resp.context["ward_table"]
        assert any(row.get_cell_value("ward") == "w1" for row in ward_table.rows)
        assert "wag_table" in resp.context

    @pytest.mark.parametrize(
        "create_deliver_units, expected_quoted",
        [
            pytest.param(False, '"services_delivery_unit", "no-children-wa"', id="both-missing"),
            pytest.param(True, "", id="both-present"),
        ],
    )
    def test_missing_deliver_units_banner(
        self, client, org_user_admin, opportunity, create_deliver_units, expected_quoted
    ):
        if create_deliver_units:
            DeliverUnitFactory(app=opportunity.deliver_app, slug=SERVICE_DELIVERY_UNIT_SLUG)
            DeliverUnitFactory(app=opportunity.deliver_app, slug=NO_CHILDREN_WORK_AREA_UNIT_SLUG)
        client.force_login(org_user_admin)
        resp = client.get(self.url(opportunity.organization.slug, str(opportunity.opportunity_id)))
        assert resp.context["quoted_missing_deliver_units"] == expected_quoted
        assert (SERVICE_DELIVERY_UNIT_SLUG.encode() in resp.content) == (not create_deliver_units)

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
        WorkAreaFactory(
            opportunity=opportunity,
            opportunity_access=OpportunityAccessFactory(opportunity=opportunity),
            ward="w1",
            status=WorkAreaStatus.VISITED,
        )
        client.force_login(org_user_admin)
        resp = client.get(
            self.url(opportunity.organization.slug, str(opportunity.opportunity_id)),
            {"export": "csv", "table": "ward"},
        )
        assert resp.status_code == 200
        assert resp["Content-Type"].startswith("text/csv")
        assert "attachment" in resp["Content-Disposition"]
        body = resp.getvalue().decode()
        assert "Expected Visit Count" in body
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


@pytest.mark.django_db
class TestClusterWorkAreas(BaseMicroplanningFlagTest):
    def url(self, org_slug, opp_id):
        return reverse("microplanning:cluster_work_areas", kwargs={"org_slug": org_slug, "opp_id": opp_id})

    @pytest.fixture
    def work_area(self, opportunity):
        return WorkAreaFactory(opportunity=opportunity)

    @patch("commcare_connect.microplanning.views.cluster_work_areas_task.delay")
    def test_valid_building_count_forwards_to_task(self, mock_delay, client, org_user_admin, opportunity, work_area):
        mock_delay.return_value = MagicMock(id="task-123")
        client.force_login(org_user_admin)

        url = self.url(opportunity.organization.slug, opportunity.opportunity_id)
        response = client.post(url, {"building_count": 250})

        assert response.status_code == 200
        mock_delay.assert_called_once_with(opportunity.id, 250)
        assert "clustering_task_id=task-123" in response.headers["HX-Push-Url"]

    @patch("commcare_connect.microplanning.views.cluster_work_areas_task.delay")
    def test_empty_building_count_defaults(self, mock_delay, client, org_user_admin, opportunity, work_area):
        mock_delay.return_value = MagicMock(id="task-123")
        client.force_login(org_user_admin)

        url = self.url(opportunity.organization.slug, opportunity.opportunity_id)
        response = client.post(url, {})

        assert response.status_code == 200
        mock_delay.assert_called_once_with(opportunity.id, 200)

    @patch("commcare_connect.microplanning.views.cluster_work_areas_task.delay")
    def test_out_of_range_does_not_start_task(self, mock_delay, client, org_user_admin, opportunity, work_area):
        client.force_login(org_user_admin)

        url = self.url(opportunity.organization.slug, opportunity.opportunity_id)
        response = client.post(url, {"building_count": 500})

        assert response.status_code == 200
        mock_delay.assert_not_called()
        assert "between 100 and 300" in response.content.decode()
        assert response.headers["HX-Retarget"] == "#building-count-field"


@pytest.mark.django_db
class TestClusterWorkAreasRerun(BaseMicroplanningFlagTest):
    def url(self, org_slug, opp_id):
        return reverse("microplanning:cluster_work_areas", args=(org_slug, opp_id))

    @patch("commcare_connect.microplanning.views.cluster_work_areas_task.delay")
    def test_rerun_replaces_existing_groups(self, mock_delay, client, org_user_admin, opportunity):

        mock_task = MagicMock()
        mock_task.id = "3f8f2e6c-0000-4000-8000-000000000000"
        mock_delay.return_value = mock_task

        old_group = WorkAreaGroupFactory(opportunity=opportunity)
        work_area = WorkAreaFactory(opportunity=opportunity, work_area_group=old_group)
        client.force_login(org_user_admin)

        response = client.post(self.url(opportunity.organization.slug, str(opportunity.opportunity_id)) + "?rerun=1")

        assert response.status_code == 200
        # Old group is discarded; the async task will produce the new grouping.
        assert not WorkAreaGroup.objects.filter(id=old_group.id).exists()
        assert mock_delay.call_count == 1
        # The work area itself survives (SET_NULL), just ungrouped until the task runs.
        work_area.refresh_from_db()
        assert work_area.work_area_group is None

    @patch("commcare_connect.microplanning.views.cluster_work_areas_task.delay")
    def test_rerun_blocked_when_assigned(self, mock_delay, client, org_user_admin, opportunity):

        old_group = WorkAreaGroupFactory(opportunity=opportunity)
        access = OpportunityAccessFactory(opportunity=opportunity)
        WorkAreaFactory(opportunity=opportunity, work_area_group=old_group, opportunity_access=access)
        client.force_login(org_user_admin)

        response = client.post(self.url(opportunity.organization.slug, str(opportunity.opportunity_id)) + "?rerun=1")

        assert response.status_code == 200
        assert "HX-Redirect" in response.headers
        assert mock_delay.call_count == 0
        # Nothing is deleted when assignments exist.
        assert WorkAreaGroup.objects.filter(id=old_group.id).exists()


@pytest.mark.django_db
class TestClearWorkAreas(BaseMicroplanningFlagTest):
    def url(self, org_slug, opp_id):
        return reverse("microplanning:clear_work_areas", args=(org_slug, opp_id))

    def test_clears_work_areas_and_groups(self, client, org_user_admin, opportunity):
        group = WorkAreaGroupFactory(opportunity=opportunity)
        WorkAreaFactory(opportunity=opportunity, work_area_group=group)
        WorkAreaFactory(opportunity=opportunity)
        client.force_login(org_user_admin)

        response = client.post(self.url(opportunity.organization.slug, str(opportunity.opportunity_id)))

        assert response.status_code == 200
        assert response.headers["HX-Redirect"].endswith(
            reverse(
                "microplanning:microplanning_home", args=(opportunity.organization.slug, opportunity.opportunity_id)
            )
        )
        assert not WorkArea.objects.filter(opportunity=opportunity).exists()
        # Groups only exist to group Work Areas, so they go too.
        assert not WorkAreaGroup.objects.filter(opportunity=opportunity).exists()
        messages = list(response.wsgi_request._messages)
        assert any("cleared" in str(m) for m in messages)

    def test_other_opportunities_are_untouched(self, client, org_user_admin, opportunity):
        other_opportunity = OpportunityFactory(organization=opportunity.organization)
        other_group = WorkAreaGroupFactory(opportunity=other_opportunity)
        other_area = WorkAreaFactory(opportunity=other_opportunity, work_area_group=other_group)
        WorkAreaFactory(opportunity=opportunity)
        client.force_login(org_user_admin)

        response = client.post(self.url(opportunity.organization.slug, str(opportunity.opportunity_id)))

        assert response.status_code == 200
        assert not WorkArea.objects.filter(opportunity=opportunity).exists()
        assert WorkArea.objects.filter(id=other_area.id).exists()
        assert WorkAreaGroup.objects.filter(id=other_group.id).exists()

    def test_implementation_areas_are_untouched(self, client, org_user_admin, opportunity):
        implementation_area = ImplementationAreaFactory(opportunity=opportunity)
        WorkAreaFactory(opportunity=opportunity, implementation_area=implementation_area)
        client.force_login(org_user_admin)

        response = client.post(self.url(opportunity.organization.slug, str(opportunity.opportunity_id)))

        assert response.status_code == 200
        assert not WorkArea.objects.filter(opportunity=opportunity).exists()
        assert ImplementationArea.objects.filter(id=implementation_area.id).exists()

    def test_clear_blocked_when_assigned(self, client, org_user_admin, opportunity):
        group = WorkAreaGroupFactory(opportunity=opportunity)
        access = OpportunityAccessFactory(opportunity=opportunity)
        work_area = WorkAreaFactory(opportunity=opportunity, work_area_group=group, opportunity_access=access)
        unassigned_area = WorkAreaFactory(opportunity=opportunity)
        client.force_login(org_user_admin)

        response = client.post(self.url(opportunity.organization.slug, str(opportunity.opportunity_id)))

        assert response.status_code == 200
        assert "HX-Redirect" in response.headers
        # A single assignment blocks the whole opportunity — nothing is deleted.
        assert WorkArea.objects.filter(id=work_area.id).exists()
        assert WorkArea.objects.filter(id=unassigned_area.id).exists()
        assert WorkAreaGroup.objects.filter(id=group.id).exists()
        messages = list(response.wsgi_request._messages)
        assert any("assigned" in str(m) for m in messages)

    def test_clear_blocked_when_visits_recorded(self, client, org_user_admin, opportunity):
        # UserVisit.work_area is PROTECT. A deleted OpportunityAccess nulls opportunity_access,
        # so a visited Work Area can look unassigned and slip past the assignment check.
        work_area = WorkAreaFactory(opportunity=opportunity, opportunity_access=None)
        UserVisitFactory(opportunity=opportunity, work_area=work_area)
        client.force_login(org_user_admin)

        response = client.post(self.url(opportunity.organization.slug, str(opportunity.opportunity_id)))

        assert response.status_code == 200
        assert "HX-Redirect" in response.headers
        assert WorkArea.objects.filter(id=work_area.id).exists()
        messages = list(response.wsgi_request._messages)
        assert any("Visits" in str(m) for m in messages)

    def test_locks_work_areas_before_checking_assignments(self, client, org_user_admin, opportunity):
        """Regression guard: the assignment check must serialize against save_assignment."""
        WorkAreaFactory(opportunity=opportunity)
        client.force_login(org_user_admin)

        with CaptureQueriesContext(connection) as ctx:
            client.post(self.url(opportunity.organization.slug, str(opportunity.opportunity_id)))

        locking_queries = [
            q["sql"]
            for q in ctx.captured_queries
            if "for update" in q["sql"].lower() and "microplanning_workarea" in q["sql"].lower()
        ]
        assert locking_queries, (
            "Expected a 'SELECT ... FOR UPDATE' on the Work Area rows so a concurrent "
            "save_assignment cannot commit between the assignment check and the delete."
        )

    def test_clear_requires_post(self, client, org_user_admin, opportunity):
        client.force_login(org_user_admin)
        response = client.get(self.url(opportunity.organization.slug, str(opportunity.opportunity_id)))
        assert response.status_code == 405

    @pytest.mark.parametrize("setup_microplanning_flag", [False], indirect=True)
    def test_clear_requires_flag(self, client, org_user_admin, opportunity):
        work_area = WorkAreaFactory(opportunity=opportunity)
        client.force_login(org_user_admin)

        response = client.post(self.url(opportunity.organization.slug, str(opportunity.opportunity_id)))

        assert response.status_code == 404
        assert WorkArea.objects.filter(id=work_area.id).exists()

    def test_clear_requires_org_admin(self, client, org_user_member, opportunity):
        work_area = WorkAreaFactory(opportunity=opportunity)
        client.force_login(org_user_member)

        response = client.post(self.url(opportunity.organization.slug, str(opportunity.opportunity_id)))

        assert response.status_code == 404
        assert WorkArea.objects.filter(id=work_area.id).exists()


@pytest.mark.django_db
class TestClearWorkAreaGroups(BaseMicroplanningFlagTest):
    def url(self, org_slug, opp_id):
        return reverse("microplanning:clear_work_area_groups", args=(org_slug, opp_id))

    def test_clears_groups_and_ungroups_work_areas(self, client, org_user_admin, opportunity):
        group = WorkAreaGroupFactory(opportunity=opportunity)
        work_area = WorkAreaFactory(opportunity=opportunity, work_area_group=group)
        client.force_login(org_user_admin)

        response = client.post(self.url(opportunity.organization.slug, str(opportunity.opportunity_id)))

        assert response.status_code == 200
        assert response.headers["HX-Redirect"].endswith(
            reverse(
                "microplanning:microplanning_home", args=(opportunity.organization.slug, opportunity.opportunity_id)
            )
        )
        assert not WorkAreaGroup.objects.filter(opportunity=opportunity).exists()
        # The polygons remain, returned to their pre-clustering ungrouped state.
        work_area.refresh_from_db()
        assert work_area.work_area_group is None
        messages = list(response.wsgi_request._messages)
        assert any("cleared" in str(m) for m in messages)

    def test_clear_blocked_when_assigned(self, client, org_user_admin, opportunity):
        group = WorkAreaGroupFactory(opportunity=opportunity)
        access = OpportunityAccessFactory(opportunity=opportunity)
        WorkAreaFactory(opportunity=opportunity, work_area_group=group, opportunity_access=access)
        client.force_login(org_user_admin)

        response = client.post(self.url(opportunity.organization.slug, str(opportunity.opportunity_id)))

        assert response.status_code == 200
        assert "HX-Redirect" in response.headers
        assert WorkAreaGroup.objects.filter(id=group.id).exists()

    def test_clear_requires_post(self, client, org_user_admin, opportunity):
        client.force_login(org_user_admin)
        response = client.get(self.url(opportunity.organization.slug, str(opportunity.opportunity_id)))
        assert response.status_code == 405
