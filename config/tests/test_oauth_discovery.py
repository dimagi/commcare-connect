import json

from django.conf import settings
from django.test import RequestFactory
from django.urls import reverse

from config.views import oauth_authorization_server

URL = "/.well-known/oauth-authorization-server"


def _get(rf=None, **extra):
    rf = rf or RequestFactory()
    return oauth_authorization_server(rf.get(URL, secure=True, **extra))


def test_discovery_returns_json_200():
    response = _get()
    assert response.status_code == 200
    assert response["Content-Type"] == "application/json"
    assert "Cache-Control" in response
    assert "max-age=3600" in response["Cache-Control"]


def test_discovery_contains_required_endpoints():
    data = json.loads(_get().content)
    for key in ("issuer", "authorization_endpoint", "token_endpoint", "introspection_endpoint"):
        assert key in data, f"missing {key}"
        assert data[key].startswith("https://"), f"{key} must be absolute HTTPS URL, got {data[key]}"


def test_discovery_response_and_grant_types():
    data = json.loads(_get().content)
    assert "code" in data["response_types_supported"]
    assert set(data["grant_types_supported"]) >= {"authorization_code", "refresh_token"}


def test_discovery_pkce_s256_only():
    data = json.loads(_get().content)
    methods = data["code_challenge_methods_supported"]
    assert "S256" in methods
    assert "plain" not in methods


def test_discovery_scopes_match_oauth2_provider_config():
    data = json.loads(_get().content)
    real_scopes = set(settings.OAUTH2_PROVIDER["SCOPES"].keys())
    advertised = set(data["scopes_supported"])
    assert advertised, "scopes_supported must not be empty"
    assert advertised <= real_scopes, f"advertised scopes not in OAUTH2_PROVIDER config: {advertised - real_scopes}"


def test_discovery_endpoints_match_oauth2_provider_urls():
    data = json.loads(_get().content)
    assert data["authorization_endpoint"].endswith(reverse("oauth2_provider:authorize"))
    assert data["token_endpoint"].endswith(reverse("oauth2_provider:token"))
    assert data["introspection_endpoint"].endswith(reverse("oauth2_provider:introspect"))
    assert data["userinfo_endpoint"].endswith(reverse("oauth2_provider:user-info"))


def test_discovery_rejects_post():
    rf = RequestFactory()
    response = oauth_authorization_server(rf.post(URL, secure=True))
    assert response.status_code == 405
