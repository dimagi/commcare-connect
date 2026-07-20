import pathlib

import pytest
from django.contrib.staticfiles import finders
from django.urls import reverse

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
