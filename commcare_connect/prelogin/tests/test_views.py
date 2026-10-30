import importlib
from contextlib import contextmanager

import pytest
from django.contrib.staticfiles import finders
from django.test import override_settings
from django.urls import clear_url_caches, reverse

from commcare_connect.prelogin import route_meta
from commcare_connect.prelogin import urls as prelogin_urls
from config import urls as root_urls

# Requests pass through CustomPGHistoryMiddleware, which opens a DB connection
# per request — so even these static-template views need DB access in tests.
pytestmark = pytest.mark.django_db


@contextmanager
def marketing_site(enabled):
    """The redirects are chosen when urls.py is imported, so rebuild the URLconf
    with the setting overridden, and again afterwards to restore it."""
    with override_settings(MARKETING_SITE_ENABLED=enabled):
        _reload_urlconf()
        yield
    _reload_urlconf()


def _reload_urlconf():
    importlib.reload(prelogin_urls)
    importlib.reload(root_urls)
    clear_url_caches()


class TestMarketingRedirects:
    """The marketing site moved under /public/ (served by the connect-marketing
    repo); every old URL here permanently redirects to its new home."""

    @pytest.fixture(autouse=True)
    def marketing_site_enabled(self):
        with marketing_site(enabled=True):
            yield

    @pytest.mark.parametrize(
        "old, new",
        [
            ("/", "/public"),
            ("/platform", "/public/platform"),
            ("/platform/", "/public/platform"),
            ("/insights", "/public/insights"),
            ("/contact/", "/public/contact"),
            ("/contact/index.html", "/public/contact"),
            ("/blog", "/public/insights"),
            ("/portfolio/kangaroo-mother-care", "/public/portfolio/kangaroo-mother-care"),
            ("/programs/reading-glasses", "/public/portfolio/reading-glasses"),
            ("/programs", "/public/portfolio"),
            ("/how-it-works", "/public/platform"),
        ],
    )
    def test_old_url_redirects(self, client, old, new):
        resp = client.get(old)
        assert resp.status_code == 301
        assert resp["Location"] == new

    def test_query_string_is_preserved(self, client):
        resp = client.get("/insights?program=kmc")
        assert resp["Location"] == "/public/insights?program=kmc"

    def test_in_action_page_still_renders(self, client):
        resp = client.get("/in-action/nigeria-cholera-response")
        assert resp.status_code == 200


class TestMarketingRedirectsDisabled:
    """Outside production there is no /public/ to redirect to."""

    @pytest.fixture(autouse=True)
    def marketing_site_disabled(self):
        with marketing_site(enabled=False):
            yield

    def test_root_goes_into_the_app(self, client):
        resp = client.get("/")
        assert resp.status_code == 302
        assert resp["Location"] == reverse("users:redirect")

    @pytest.mark.parametrize("old", ["/platform", "/contact/", "/portfolio/kangaroo-mother-care"])
    def test_old_marketing_urls_404(self, client, old):
        assert client.get(old).status_code == 404


class TestRouteMeta:
    """The head is rendered server-side, per route.

    Link unfurlers and non-rendering crawlers never run app.js, so these tags
    have to be correct in the response body itself, not after JavaScript.
    """

    def test_social_image_is_a_real_static_file(self):
        """The old hardcoded og:image was missing the /static/ prefix and 404d,
        so every share of this site had a broken preview."""
        assert finders.find(route_meta.DEFAULT_IMAGE) is not None
        for route, meta in route_meta.ROUTES.items():
            if "image" in meta:
                assert finders.find(meta["image"]) is not None, route

    @pytest.mark.parametrize("route", sorted(route_meta.ROUTES))
    def test_every_route_has_a_distinct_title(self, route):
        meta = route_meta.meta_for(route, lambda p: "/static/" + p)
        assert meta["title"]
        assert meta["description"]
        if route != "/":
            assert meta["title"] != route_meta.ROUTES["/"]["title"], route

    def test_unknown_route_falls_back_to_home(self):
        meta = route_meta.meta_for("/not-a-page", lambda p: "/static/" + p)
        assert meta["title"] == route_meta.ROUTES["/"]["title"]
        # The router renders home for an unknown path, so the canonical must
        # say "/" rather than claim the unknown URL as a page of its own.
        assert meta["canonical"] == route_meta.SITE_ORIGIN + "/"

    def test_trailing_slash_resolves_to_the_same_route(self):
        get = lambda p: route_meta.meta_for(p, lambda s: "/static/" + s)  # noqa: E731
        assert get("/insights/")["title"] == get("/insights")["title"]
