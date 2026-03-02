import ast
import csv
import datetime
import io

import pytest
from django.urls import reverse
from django.utils.timezone import now

from commcare_connect.opportunity.tests.factories import BlobMetaFactory, UserVisitFactory


def add_export_credentials(api_client, user):
    token, _ = user.oauth2_provider_accesstoken.get_or_create(
        token="export_token",
        scope="export",
        defaults={"expires": now() + datetime.timedelta(hours=1)},
        application=None,
    )
    api_client.credentials(Authorization=f"Bearer {token}")


def parse_streaming_csv(response):
    content = b"".join(response.streaming_content)
    return list(csv.DictReader(io.StringIO(content.decode())))


@pytest.mark.django_db
class TestUserVisitDataView:
    def url(self, opportunity):
        return reverse("data_export:user_visit_data", kwargs={"opp_id": opportunity.id})

    def test_unauthenticated_returns_401(self, api_client, opportunity):
        response = api_client.get(self.url(opportunity))
        assert response.status_code == 401

    def test_wrong_opportunity_returns_404(self, api_client, org_user_admin, opportunity):
        add_export_credentials(api_client, org_user_admin)
        url = reverse("data_export:user_visit_data", kwargs={"opp_id": opportunity.id + 9999})
        response = api_client.get(url)
        assert response.status_code == 404

    def test_returns_csv_with_correct_row_count(self, api_client, opportunity, org_user_admin):
        add_export_credentials(api_client, org_user_admin)
        UserVisitFactory.create_batch(5, opportunity=opportunity)

        response = api_client.get(self.url(opportunity))

        assert response.status_code == 200
        assert "text/csv" in response["Content-Type"]
        rows = parse_streaming_csv(response)
        assert len(rows) == 5

    def test_visits_with_images_are_exported(self, api_client, opportunity, org_user_admin):
        add_export_credentials(api_client, org_user_admin)
        visit = UserVisitFactory(opportunity=opportunity)
        blob1 = BlobMetaFactory(parent_id=visit.xform_id, content_type="image/jpeg")
        blob2 = BlobMetaFactory(parent_id=visit.xform_id, content_type="image/png")

        response = api_client.get(self.url(opportunity))
        rows = parse_streaming_csv(response)

        assert len(rows) == 1
        images = ast.literal_eval(rows[0]["images"])
        blob_ids = {img["blob_id"] for img in images}
        assert blob1.blob_id in blob_ids
        assert blob2.blob_id in blob_ids

    def test_non_image_blobs_excluded(self, api_client, opportunity, org_user_admin):
        add_export_credentials(api_client, org_user_admin)
        visit = UserVisitFactory(opportunity=opportunity)
        BlobMetaFactory(parent_id=visit.xform_id, content_type="image/jpeg")
        BlobMetaFactory(parent_id=visit.xform_id, content_type="application/pdf")

        response = api_client.get(self.url(opportunity))
        rows = parse_streaming_csv(response)

        images = ast.literal_eval(rows[0]["images"])
        assert len(images) == 1
        assert images[0]["blob_id"] != ""

    def test_visits_without_images_have_empty_list(self, api_client, opportunity, org_user_admin):
        add_export_credentials(api_client, org_user_admin)
        UserVisitFactory(opportunity=opportunity)

        response = api_client.get(self.url(opportunity))
        rows = parse_streaming_csv(response)

        assert rows[0]["images"] == "[]"

    def test_images_scoped_to_each_visit(self, api_client, opportunity, org_user_admin):
        add_export_credentials(api_client, org_user_admin)
        visit1 = UserVisitFactory(opportunity=opportunity)
        visit2 = UserVisitFactory(opportunity=opportunity)
        blob1 = BlobMetaFactory(parent_id=visit1.xform_id, content_type="image/jpeg")
        blob2 = BlobMetaFactory(parent_id=visit2.xform_id, content_type="image/jpeg")

        response = api_client.get(self.url(opportunity))
        rows = parse_streaming_csv(response)

        all_images_by_xform = {visit1.xform_id: blob1.blob_id, visit2.xform_id: blob2.blob_id}
        for row in rows:
            images = ast.literal_eval(row["images"])
            assert len(images) == 1
            expected_blob_id = all_images_by_xform[row["xform_id"]]
            assert images[0]["blob_id"] == expected_blob_id
