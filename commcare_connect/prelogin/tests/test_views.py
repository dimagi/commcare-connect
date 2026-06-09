import pytest
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

    def test_login_url_defaults_to_accounts_login(self, client):
        resp = client.get(reverse("prelogin:home"))
        assert resp.context["app_login_url"] == "/accounts/login/"

    def test_login_url_respects_setting_override(self, client, settings):
        settings.PRELOGIN_APP_LOGIN_URL = "/custom/login/"
        resp = client.get(reverse("prelogin:home"))
        assert resp.context["app_login_url"] == "/custom/login/"


class TestMarketingRoutes:
    """Every clean-URL route renders the SPA template server-side so a direct
    load / refresh doesn't 404 (the client router handles in-page nav)."""

    # Derive names from MARKETING_ROUTES (urls.py) so this can't drift from the
    # actual route table; "" is registered under the name "home".
    @pytest.mark.parametrize("name", [route or "home" for route in MARKETING_ROUTES])
    def test_marketing_route_renders(self, client, name):
        resp = client.get(reverse(f"prelogin:{name}"))
        assert resp.status_code == 200
        assert resp.context["app_login_url"] == "/accounts/login/"

    def test_portfolio_detail_renders(self, client):
        resp = client.get("/portfolio/kangaroo-mother-care")
        assert resp.status_code == 200
        assert b"Connect by Dimagi" in resp.content
