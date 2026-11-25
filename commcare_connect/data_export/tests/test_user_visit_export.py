import csv
import io
from collections.abc import Callable
from typing import NamedTuple

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from commcare_connect.data_export.tests.helpers import add_export_credentials, export_url
from commcare_connect.opportunity.tests.factories import (
    AudioAttachmentFactory,
    BlobMetaFactory,
    UserVisitFactory,
)


@pytest.fixture
def api_client_v2(api_client):
    # Merged rather than replaced, so this works either side of add_export_credentials().
    credentials = getattr(api_client, "_credentials", {})
    api_client.credentials(**{**credentials, "HTTP_ACCEPT": "application/json; version=2.0"})
    return api_client


@pytest.fixture
def authed_visit(api_client, opportunity, org_user_member):
    """A visit in ``opportunity``, with ``api_client`` authorized to export that opportunity."""
    add_export_credentials(api_client, org_user_member)
    return UserVisitFactory(opportunity=opportunity, user=org_user_member)


def _visits_url(opportunity, **params):
    return export_url(url_name="user_visit_data", opp_id=opportunity.id, **params)


def _parse_csv_response(response):
    content = b"".join(response.streaming_content).decode()
    reader = csv.DictReader(io.StringIO(content))
    return list(reader), reader.fieldnames


def _attach_image(visit, name="photo.jpg"):
    return BlobMetaFactory(parent_id=visit.xform_id, content_type="image/jpeg", name=name)


def _attach_audio(visit, name="q1.m4a"):
    return AudioAttachmentFactory(user_visit=visit, name=name)


class AttachmentCase(NamedTuple):
    """One attachment list the export can include, and what it takes to exercise it."""

    field: str
    attach: Callable
    name: str
    table: str  # the one table a batched prefetch is expected to hit


ATTACHMENT_CASES = [
    pytest.param(AttachmentCase("images", _attach_image, "photo.jpg", "opportunity_blobmeta"), id="images"),
    pytest.param(AttachmentCase("audio", _attach_audio, "q1.m4a", "opportunity_audioattachment"), id="audio"),
]
attachment_cases = pytest.mark.parametrize("case", ATTACHMENT_CASES)


@pytest.mark.django_db
class TestUserVisitDataViewV1:
    def test_default_excludes_attachment_columns(self, api_client, opportunity, authed_visit):
        _attach_image(authed_visit)
        _attach_audio(authed_visit)

        response = api_client.get(_visits_url(opportunity))
        assert response.status_code == 200

        rows, fieldnames = _parse_csv_response(response)
        assert "images" not in fieldnames
        assert "audio" not in fieldnames
        assert len(rows) == 1

    @attachment_cases
    def test_true_includes_column(self, api_client, opportunity, authed_visit, case):
        case.attach(authed_visit)

        response = api_client.get(_visits_url(opportunity, **{case.field: "true"}))
        assert response.status_code == 200

        rows, fieldnames = _parse_csv_response(response)
        assert case.field in fieldnames
        assert len(rows) == 1
        assert case.name in rows[0][case.field]

    @attachment_cases
    def test_false_excludes_column(self, api_client, opportunity, authed_visit, case):
        case.attach(authed_visit)

        response = api_client.get(_visits_url(opportunity, **{case.field: "false"}))
        assert response.status_code == 200

        _, fieldnames = _parse_csv_response(response)
        assert case.field not in fieldnames

    @attachment_cases
    def test_column_empty_with_no_attachments(self, api_client, opportunity, authed_visit, case):
        response = api_client.get(_visits_url(opportunity, **{case.field: "true"}))
        assert response.status_code == 200

        rows, fieldnames = _parse_csv_response(response)
        assert case.field in fieldnames
        assert rows[0][case.field] == "[]"

    @attachment_cases
    def test_prefetch_avoids_n_plus_1(self, api_client, opportunity, authed_visit, case):
        for visit in [authed_visit, *UserVisitFactory.create_batch(4, opportunity=opportunity)]:
            case.attach(visit)

        with CaptureQueriesContext(connection) as ctx:
            response = api_client.get(_visits_url(opportunity, **{case.field: "true"}))
            _ = b"".join(response.streaming_content)

        queries = [q["sql"] for q in ctx.captured_queries if case.table in q["sql"]]
        assert len(queries) == 1, f"Expected 1 {case.table} query, got {len(queries)}: {queries}"

    @attachment_cases
    def test_streams_attachments_past_the_first_batch(self, api_client, opportunity, authed_visit, case, monkeypatch):
        """Every batch prefetches its own attachments, so later rows are not left empty."""
        visits = [authed_visit, *UserVisitFactory.create_batch(4, opportunity=opportunity)]
        expected = [f"q{i}-{case.name}" for i in range(len(visits))]
        for visit, name in zip(visits, expected):
            case.attach(visit, name=name)
        monkeypatch.setattr("commcare_connect.data_export.views.STREAM_CHUNK_SIZE", 2)

        response = api_client.get(_visits_url(opportunity, **{case.field: "true"}))
        rows, _ = _parse_csv_response(response)

        # The streamed rows come back in no particular order, so each attachment is only
        # required to appear against exactly one of them.
        assert len(rows) == len(visits)
        column = [row[case.field] for row in rows]
        for name in expected:
            assert sum(name in value for value in column) == 1, f"{name} not found in exactly one row: {column}"

    def test_non_image_blobs_excluded(self, api_client, opportunity, authed_visit):
        BlobMetaFactory(parent_id=authed_visit.xform_id, content_type="application/pdf", name="doc.pdf")
        _attach_image(authed_visit)

        response = api_client.get(_visits_url(opportunity, images="true"))
        rows, _ = _parse_csv_response(response)

        assert "photo.jpg" in rows[0]["images"]
        assert "doc.pdf" not in rows[0]["images"]

    def test_audio_includes_transcript_and_translation(self, api_client, opportunity, authed_visit):
        AudioAttachmentFactory(user_visit=authed_visit, name="q1.m4a", transcript="namaste", translation="hello")

        response = api_client.get(_visits_url(opportunity, audio="true"))
        rows, _ = _parse_csv_response(response)

        assert "q1.m4a" in rows[0]["audio"]
        assert "namaste" in rows[0]["audio"]
        assert "hello" in rows[0]["audio"]

    def test_images_and_audio_together(self, api_client, opportunity, authed_visit):
        _attach_image(authed_visit)
        _attach_audio(authed_visit)

        response = api_client.get(_visits_url(opportunity, images="true", audio="true"))

        rows, fieldnames = _parse_csv_response(response)
        assert {"images", "audio"} <= set(fieldnames)
        assert "photo.jpg" in rows[0]["images"]
        assert "q1.m4a" in rows[0]["audio"]


@pytest.mark.django_db
class TestUserVisitDataViewV2:
    def test_returns_paginated_json(self, api_client_v2, opportunity, authed_visit):
        UserVisitFactory.create_batch(2, opportunity=opportunity)

        response = api_client_v2.get(_visits_url(opportunity, page_size=2))
        assert response.status_code == 200

        data = response.json()
        assert "next" in data
        assert len(data["results"]) == 2

    def test_default_excludes_attachment_fields(self, api_client_v2, opportunity, authed_visit):
        _attach_image(authed_visit)
        _attach_audio(authed_visit)

        response = api_client_v2.get(_visits_url(opportunity))

        row = response.json()["results"][0]
        assert "images" not in row
        assert "audio" not in row

    @attachment_cases
    def test_true_includes_only_that_field(self, api_client_v2, opportunity, authed_visit, case):
        case.attach(authed_visit)

        response = api_client_v2.get(_visits_url(opportunity, **{case.field: "true"}))

        row = response.json()["results"][0]
        assert [attachment["name"] for attachment in row[case.field]] == [case.name]
        assert len(row.keys() & {"images", "audio"}) == 1

    @attachment_cases
    def test_field_empty_with_no_attachments(self, api_client_v2, opportunity, authed_visit, case):
        response = api_client_v2.get(_visits_url(opportunity, **{case.field: "true"}))
        assert response.status_code == 200

        assert response.json()["results"][0][case.field] == []

    @attachment_cases
    def test_prefetch_avoids_n_plus_1(self, api_client_v2, opportunity, authed_visit, case):
        for visit in [authed_visit, *UserVisitFactory.create_batch(4, opportunity=opportunity)]:
            case.attach(visit)

        with CaptureQueriesContext(connection) as ctx:
            api_client_v2.get(_visits_url(opportunity, **{case.field: "true"}))

        queries = [q["sql"] for q in ctx.captured_queries if case.table in q["sql"]]
        assert len(queries) == 1, f"Expected 1 {case.table} query, got {len(queries)}: {queries}"

    def test_image_fields(self, api_client_v2, opportunity, authed_visit):
        blob = _attach_image(authed_visit)

        response = api_client_v2.get(_visits_url(opportunity, images="true"))

        assert response.json()["results"][0]["images"] == [
            {
                "blob_id": blob.blob_id,
                "parent_id": authed_visit.xform_id,
                "name": "photo.jpg",
            }
        ]

    def test_non_image_blobs_excluded(self, api_client_v2, opportunity, authed_visit):
        BlobMetaFactory(parent_id=authed_visit.xform_id, content_type="application/pdf", name="doc.pdf")
        _attach_image(authed_visit)

        response = api_client_v2.get(_visits_url(opportunity, images="true"))

        row = response.json()["results"][0]
        assert [image["name"] for image in row["images"]] == ["photo.jpg"]

    def test_audio_fields_include_transcript_and_translation(self, api_client_v2, opportunity, authed_visit):
        audio = AudioAttachmentFactory(
            user_visit=authed_visit, name="q1.m4a", transcript="namaste", translation="hello"
        )

        response = api_client_v2.get(_visits_url(opportunity, audio="true"))

        assert response.json()["results"][0]["audio"] == [
            {
                "blob_id": audio.blob_id,
                "name": "q1.m4a",
                "content_type": "audio/mp4",
                "transcript": "namaste",
                "translation": "hello",
            }
        ]

    def test_audio_lists_every_attachment_on_the_visit(self, api_client_v2, opportunity, authed_visit):
        names = ["q1.m4a", "q2.m4a", "q3.m4a"]
        for name in names:
            _attach_audio(authed_visit, name=name)

        response = api_client_v2.get(_visits_url(opportunity, audio="true"))

        row = response.json()["results"][0]
        assert sorted(attachment["name"] for attachment in row["audio"]) == names

    def test_images_and_audio_together(self, api_client_v2, opportunity, authed_visit):
        _attach_image(authed_visit)
        _attach_audio(authed_visit)

        response = api_client_v2.get(_visits_url(opportunity, images="true", audio="true"))

        row = response.json()["results"][0]
        assert [image["name"] for image in row["images"]] == ["photo.jpg"]
        assert [attachment["name"] for attachment in row["audio"]] == ["q1.m4a"]

    def test_includes_user_id_and_user_visit_id(self, api_client_v2, opportunity, org_user_member, authed_visit):
        response = api_client_v2.get(_visits_url(opportunity))

        data = response.json()["results"][0]
        assert data["user_id"] == str(org_user_member.user_id)
        assert data["user_visit_id"] == str(authed_visit.user_visit_id)

    def test_pagination_traversal(self, api_client_v2, opportunity, authed_visit):
        UserVisitFactory.create_batch(4, opportunity=opportunity)

        all_results = []
        url = _visits_url(opportunity, page_size=2)
        while url:
            response = api_client_v2.get(url)
            assert response.status_code == 200
            data = response.json()
            all_results.extend(data["results"])
            url = data["next"]

        assert len(all_results) == 5
        ids = [r["id"] for r in all_results]
        assert len(set(ids)) == 5
