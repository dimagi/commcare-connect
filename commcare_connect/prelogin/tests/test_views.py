import json
import pathlib
import re

import pytest
from django.contrib.staticfiles import finders
from django.urls import reverse

from commcare_connect.prelogin import route_meta
from commcare_connect.prelogin.urls import MARKETING_ROUTES

# Requests pass through CustomPGHistoryMiddleware, which opens a DB connection
# per request — so even these static-template views need DB access in tests.
pytestmark = pytest.mark.django_db


class TestPreloginHome:
    def test_renders_with_brand(self, client):
        resp = client.get(reverse("prelogin:home"))
        assert resp.status_code == 200
        assert b"Connect by Dimagi" in resp.content


class TestMarketingRoutes:
    """Every clean-URL route renders the SPA template server-side so a direct
    load / refresh doesn't 404 (the client router handles in-page nav)."""

    # Derive names from MARKETING_ROUTES (urls.py) so this can't drift from the
    # actual route table; "" is registered under the name "home".
    @pytest.mark.parametrize("name", [route or "home" for route in MARKETING_ROUTES])
    def test_marketing_route_renders(self, client, name):
        resp = client.get(reverse(f"prelogin:{name}"))
        assert resp.status_code == 200

    def test_portfolio_detail_renders(self, client):
        resp = client.get("/portfolio/kangaroo-mother-care")
        assert resp.status_code == 200
        assert b"Connect by Dimagi" in resp.content


class TestContactPage:
    @pytest.mark.parametrize("url", ["/contact/", "/contact/index.html"])
    def test_contact_renders(self, client, url):
        resp = client.get(url)
        assert resp.status_code == 200
        assert b"Talk to the" in resp.content

    def test_contact_has_hubspot_form(self, client):
        resp = client.get("/contact/")
        assert b"contact-form.js" in resp.content
        assert b'id="hubspot-form"' in resp.content

    def test_contact_form_js_hubspot_config(self):
        path = finders.find("prelogin/contact-form.js")
        assert path is not None, "contact-form.js not found in static files"
        content = pathlib.Path(path).read_text()
        assert "503070" in content  # portalId
        assert "ca08edba-5d8f-4386-b5e9-d6b026c14599" in content  # formId


class TestBlogManifest:
    def test_returns_hardcoded_static_file(self, client):
        resp = client.get(reverse("prelogin:blog-manifest"))
        assert resp.status_code == 200
        assert resp["Access-Control-Allow-Origin"] == "https://dimagi.com"
        data = json.loads(resp.content)
        assert data["count"] == len(data["posts"])
        assert data["posts"][0]["slug"]


class TestRouteMeta:
    """The head is rendered server-side, per route.

    Link unfurlers and non-rendering crawlers never run app.js, so these tags
    have to be correct in the response body itself, not after JavaScript.
    """

    def test_home_head_is_the_home_entry(self, client):
        resp = client.get(reverse("prelogin:home"))
        assert b"<title>Connect by Dimagi | Pay for verified service delivery</title>" in resp.content
        assert b'<link rel="canonical" href="https://connect.dimagi.com/">' in resp.content

    def test_blog_post_head_is_the_post_not_the_home_page(self, client):
        resp = client.get("/blog/connect-2023-year-in-review")
        assert b"Connect: a year in review" in resp.content
        assert b'href="https://connect.dimagi.com/blog/connect-2023-year-in-review"' in resp.content
        # og:type distinguishes a post from a site page for unfurlers.
        assert b'<meta property="og:type" content="article">' in resp.content

    def test_blog_post_carries_its_own_social_image(self, client):
        resp = client.get("/blog/connect-2023-year-in-review")
        # Scoped to the og:image tag: the default image legitimately still
        # appears further down, inside the route table app.js reads.
        og_image = re.search(rb'<meta property="og:image" content="([^"]*)"', resp.content)
        assert og_image, "no og:image rendered"
        assert b"images/blog/connect-2023-year-in-review.jpg" in og_image.group(1)

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

    def test_client_table_is_served_for_the_router(self, client):
        """app.js reads this instead of carrying its own copy of the table."""
        resp = client.get(reverse("prelogin:home"))
        assert b'id="route-meta"' in resp.content
        assert resp.context["route_meta"].keys() == route_meta.ROUTES.keys()
