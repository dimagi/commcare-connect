import uuid
from unittest.mock import patch

from django.http import HttpResponse
from django.views.i18n import JavaScriptCatalog

from config.views import javascript_catalog_view


def _release():
    # Tests share a Redis database that outlives them, so each release must be new.
    return f"test-{uuid.uuid4()}"


def test_cached_for_a_day_when_there_is_a_release(rf):
    response = javascript_catalog_view(_release())(rf.get("/jsi18n/"))

    assert response.status_code == 200
    assert "max-age=86400" in response["Cache-Control"]


def test_not_cached_without_a_release(rf):
    response = javascript_catalog_view(None)(rf.get("/jsi18n/"))

    assert response.status_code == 200
    assert not response.has_header("Cache-Control")


def test_each_release_has_its_own_cache_entry(rf):
    first, second = javascript_catalog_view(_release()), javascript_catalog_view(_release())

    with patch.object(JavaScriptCatalog, "get", return_value=HttpResponse("catalog")) as render:
        first(rf.get("/jsi18n/"))
        first(rf.get("/jsi18n/"))
        assert render.call_count == 1  # second request served from the cache

        second(rf.get("/jsi18n/"))
        assert render.call_count == 2  # a new release doesn't reuse the old entry
