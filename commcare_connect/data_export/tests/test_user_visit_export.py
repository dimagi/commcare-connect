import csv
import datetime
import io

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils.timezone import now

from commcare_connect.opportunity.tests.factories import BlobMetaFactory, UserVisitFactory


def _add_export_credentials(api_client, user):
    token, _ = user.oauth2_provider_accesstoken.get_or_create(
        token="export-token",
        scope="read write export",
        defaults={"expires": now() + datetime.timedelta(hours=1)},
    )
    api_client.credentials(**{**getattr(api_client, "_credentials", {}), "Authorization": f"Bearer {token}"})


@pytest.fixture
def api_client_v2(api_client):
    api_client.credentials(HTTP_ACCEPT="application/json; version=2.0")
    return api_client


def _parse_csv_response(response):
    content = b"".join(response.streaming_content).decode()
    reader = csv.DictReader(io.StringIO(content))
    return list(reader), reader.fieldnames


def _get_url(opp_id, **params):
    url = reverse("data_export:user_visit_data", kwargs={"opp_id": opp_id})
    if params:
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{url}?{qs}"
    return url


@pytest.mark.django_db
class TestUserVisitDataViewV1:
    def test_default_excludes_images_column(self, api_client, opportunity, org_user_member):
        UserVisitFactory(opportunity=opportunity, user=org_user_member)
        _add_export_credentials(api_client, org_user_member)

        response = api_client.get(_get_url(opportunity.id))
        assert response.status_code == 200

        rows, fieldnames = _parse_csv_response(response)
        assert "images" not in fieldnames
        assert len(rows) == 1

    def test_images_true_includes_images_column(self, api_client, opportunity, org_user_member):
        visit = UserVisitFactory(opportunity=opportunity, user=org_user_member)
        BlobMetaFactory(parent_id=visit.xform_id, content_type="image/jpeg", name="photo.jpg")
        _add_export_credentials(api_client, org_user_member)

        response = api_client.get(_get_url(opportunity.id, images="true"))
        assert response.status_code == 200

        rows, fieldnames = _parse_csv_response(response)
        assert "images" in fieldnames
        assert len(rows) == 1
        assert "photo.jpg" in rows[0]["images"]

    def test_images_false_excludes_images_column(self, api_client, opportunity, org_user_member):
        UserVisitFactory(opportunity=opportunity, user=org_user_member)
        _add_export_credentials(api_client, org_user_member)

        response = api_client.get(_get_url(opportunity.id, images="false"))
        assert response.status_code == 200

        rows, fieldnames = _parse_csv_response(response)
        assert "images" not in fieldnames

    def test_images_prefetch_avoids_n_plus_1(self, api_client, opportunity, org_user_member):
        visits = UserVisitFactory.create_batch(5, opportunity=opportunity, user=org_user_member)
        for visit in visits:
            BlobMetaFactory(parent_id=visit.xform_id, content_type="image/png", name="img.png")

        _add_export_credentials(api_client, org_user_member)

        with CaptureQueriesContext(connection) as ctx:
            response = api_client.get(_get_url(opportunity.id, images="true"))
            _ = b"".join(response.streaming_content)

        blob_queries = [q["sql"] for q in ctx.captured_queries if "opportunity_blobmeta" in q["sql"]]
        assert len(blob_queries) == 1, f"Expected 1 BlobMeta query, got {len(blob_queries)}: {blob_queries}"

    def test_images_with_no_blobs(self, api_client, opportunity, org_user_member):
        UserVisitFactory(opportunity=opportunity, user=org_user_member)
        _add_export_credentials(api_client, org_user_member)

        response = api_client.get(_get_url(opportunity.id, images="true"))
        assert response.status_code == 200

        rows, fieldnames = _parse_csv_response(response)
        assert "images" in fieldnames
        assert rows[0]["images"] == "[]"

    def test_non_image_blobs_excluded(self, api_client, opportunity, org_user_member):
        visit = UserVisitFactory(opportunity=opportunity, user=org_user_member)
        BlobMetaFactory(parent_id=visit.xform_id, content_type="application/pdf", name="doc.pdf")
        BlobMetaFactory(parent_id=visit.xform_id, content_type="image/jpeg", name="photo.jpg")
        _add_export_credentials(api_client, org_user_member)

        response = api_client.get(_get_url(opportunity.id, images="true"))
        rows, _ = _parse_csv_response(response)

        assert "photo.jpg" in rows[0]["images"]
        assert "doc.pdf" not in rows[0]["images"]


@pytest.mark.django_db
class TestUserVisitDataViewV2:
    def test_returns_paginated_json(self, api_client_v2, opportunity, org_user_member):
        UserVisitFactory.create_batch(3, opportunity=opportunity, user=org_user_member)
        _add_export_credentials(api_client_v2, org_user_member)

        response = api_client_v2.get(_get_url(opportunity.id, page_size=2))

        assert response.status_code == 200

        data = response.json()
        assert "results" in data
        assert "next" in data
        assert len(data["results"]) == 2

    def test_default_excludes_images_column(self, api_client_v2, opportunity, org_user_member):
        UserVisitFactory(opportunity=opportunity, user=org_user_member)
        _add_export_credentials(api_client_v2, org_user_member)

        response = api_client_v2.get(_get_url(opportunity.id))

        data = response.json()

        assert "images" not in data["results"][0]

    def test_images_true_includes_images_column(self, api_client_v2, opportunity, org_user_member):
        visit = UserVisitFactory(opportunity=opportunity, user=org_user_member)
        blob = BlobMetaFactory(parent_id=visit.xform_id, content_type="image/jpeg", name="photo.jpg")

        _add_export_credentials(api_client_v2, org_user_member)

        response = api_client_v2.get(_get_url(opportunity.id, images="true"))

        data = response.json()

        assert "images" in data["results"][0]
        assert data["results"][0]["images"][0] == {
            "blob_id": blob.blob_id,
            "parent_id": visit.xform_id,
            "name": "photo.jpg",
        }

    def test_images_prefetch_avoids_n_plus_1(self, api_client_v2, opportunity, org_user_member):
        visits = UserVisitFactory.create_batch(5, opportunity=opportunity, user=org_user_member)
        for visit in visits:
            BlobMetaFactory(parent_id=visit.xform_id, content_type="image/png", name="img.png")

        _add_export_credentials(api_client_v2, org_user_member)

        with CaptureQueriesContext(connection) as ctx:
            api_client_v2.get(_get_url(opportunity.id, images="true"))

        blob_queries = [q["sql"] for q in ctx.captured_queries if "opportunity_blobmeta" in q["sql"]]
        assert len(blob_queries) == 1, f"Expected 1 BlobMeta query, got {len(blob_queries)}: {blob_queries}"

    def test_images_with_no_blobs(self, api_client_v2, opportunity, org_user_member):
        UserVisitFactory(opportunity=opportunity, user=org_user_member)
        _add_export_credentials(api_client_v2, org_user_member)

        response = api_client_v2.get(_get_url(opportunity.id, images="true"))
        assert response.status_code == 200

        data = response.json()
        assert "images" in data["results"][0]
        assert not data["results"][0]["images"]

    def test_non_image_blobs_excluded(self, api_client_v2, opportunity, org_user_member):
        visit = UserVisitFactory(opportunity=opportunity, user=org_user_member)
        BlobMetaFactory(parent_id=visit.xform_id, content_type="application/pdf", name="doc.pdf")
        image_blob = BlobMetaFactory(parent_id=visit.xform_id, content_type="image/jpeg", name="photo.jpg")
        _add_export_credentials(api_client_v2, org_user_member)

        response = api_client_v2.get(_get_url(opportunity.id, images="true"))
        data = response.json()

        assert data["results"][0]["images"] == [
            {
                "blob_id": image_blob.blob_id,
                "parent_id": visit.xform_id,
                "name": "photo.jpg",
            }
        ]

    def test_pagination_traversal(self, api_client_v2, opportunity, org_user_member):
        UserVisitFactory.create_batch(5, opportunity=opportunity, user=org_user_member)
        _add_export_credentials(api_client_v2, org_user_member)

        all_results = []
        url = _get_url(opportunity.id, page_size=2)
        while url:
            response = api_client_v2.get(url)
            assert response.status_code == 200
            data = response.json()
            all_results.extend(data["results"])
            url = data["next"]

        assert len(all_results) == 5
        ids = [r["id"] for r in all_results]
        assert len(set(ids)) == 5
