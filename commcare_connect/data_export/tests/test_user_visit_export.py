import csv
import datetime
import io

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils.timezone import now

from commcare_connect.opportunity.tests.factories import BlobMetaFactory, UserVisitFactory


def _add_export_credentials(api_client, user):
    token, _ = user.oauth2_provider_accesstoken.get_or_create(
        token="export-token",
        scope="read write export",
        defaults={"expires": now() + datetime.timedelta(hours=1)},
    )
    api_client.credentials(Authorization=f"Bearer {token}")


def _parse_csv_response(response):
    content = b"".join(response.streaming_content).decode()
    reader = csv.DictReader(io.StringIO(content))
    return list(reader), reader.fieldnames


@pytest.mark.django_db
class TestUserVisitDataView:
    def _get_url(self, opp_id, **params):
        from django.urls import reverse

        url = reverse("data_export:user_visit_data", kwargs={"opp_id": opp_id})
        if params:
            qs = "&".join(f"{k}={v}" for k, v in params.items())
            url = f"{url}?{qs}"
        return url

    def test_default_excludes_images_column(self, api_client, opportunity, org_user_member):
        UserVisitFactory(opportunity=opportunity, user=org_user_member)
        _add_export_credentials(api_client, org_user_member)

        response = api_client.get(self._get_url(opportunity.id))
        assert response.status_code == 200

        rows, fieldnames = _parse_csv_response(response)
        assert "images" not in fieldnames
        assert len(rows) == 1

    def test_images_true_includes_images_column(self, api_client, opportunity, org_user_member):
        visit = UserVisitFactory(opportunity=opportunity, user=org_user_member)
        BlobMetaFactory(parent_id=visit.xform_id, content_type="image/jpeg", name="photo.jpg")
        _add_export_credentials(api_client, org_user_member)

        response = api_client.get(self._get_url(opportunity.id, images="true"))
        assert response.status_code == 200

        rows, fieldnames = _parse_csv_response(response)
        assert "images" in fieldnames
        assert len(rows) == 1
        assert "photo.jpg" in rows[0]["images"]

    def test_images_false_excludes_images_column(self, api_client, opportunity, org_user_member):
        UserVisitFactory(opportunity=opportunity, user=org_user_member)
        _add_export_credentials(api_client, org_user_member)

        response = api_client.get(self._get_url(opportunity.id, images="false"))
        assert response.status_code == 200

        rows, fieldnames = _parse_csv_response(response)
        assert "images" not in fieldnames

    def test_images_prefetch_avoids_n_plus_1(self, api_client, opportunity, org_user_member):
        visits = UserVisitFactory.create_batch(5, opportunity=opportunity, user=org_user_member)
        for visit in visits:
            BlobMetaFactory(parent_id=visit.xform_id, content_type="image/png", name="img.png")

        _add_export_credentials(api_client, org_user_member)

        with CaptureQueriesContext(connection) as ctx:
            response = api_client.get(self._get_url(opportunity.id, images="true"))
            _ = b"".join(response.streaming_content)

        blob_queries = [q["sql"] for q in ctx.captured_queries if "opportunity_blobmeta" in q["sql"]]
        assert len(blob_queries) == 1, f"Expected 1 BlobMeta query, got {len(blob_queries)}: {blob_queries}"

    def test_images_with_no_blobs(self, api_client, opportunity, org_user_member):
        UserVisitFactory(opportunity=opportunity, user=org_user_member)
        _add_export_credentials(api_client, org_user_member)

        response = api_client.get(self._get_url(opportunity.id, images="true"))
        assert response.status_code == 200

        rows, fieldnames = _parse_csv_response(response)
        assert "images" in fieldnames
        assert rows[0]["images"] == "[]"

    def test_non_image_blobs_excluded(self, api_client, opportunity, org_user_member):
        visit = UserVisitFactory(opportunity=opportunity, user=org_user_member)
        BlobMetaFactory(parent_id=visit.xform_id, content_type="application/pdf", name="doc.pdf")
        BlobMetaFactory(parent_id=visit.xform_id, content_type="image/jpeg", name="photo.jpg")
        _add_export_credentials(api_client, org_user_member)

        response = api_client.get(self._get_url(opportunity.id, images="true"))
        rows, _ = _parse_csv_response(response)

        assert "photo.jpg" in rows[0]["images"]
        assert "doc.pdf" not in rows[0]["images"]
