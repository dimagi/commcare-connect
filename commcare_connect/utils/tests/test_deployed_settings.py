import json
import subprocess
import sys

import pytest
from django.conf import settings
from django.core.management import call_command
from django.test import override_settings

DEPLOYED_SETTINGS_MODULES = ["config.settings.staging", "config.settings.production"]


def _load_deployed_settings(module):
    # Imported in a subprocess: deploy_base.py mutates base.py's MIDDLEWARE list in place, which would alter the
    # settings of the running test session. read_env is stubbed out so a developer's .env can't override the
    # defaults under test; deployed environments have no .env either.
    script = (
        "import environ; environ.Env.read_env = staticmethod(lambda *args, **kwargs: None); "
        f"import json, {module} as s; "
        "print(json.dumps({"
        "'DEBUG': s.DEBUG, "
        "'SESSION_COOKIE_SECURE': s.SESSION_COOKIE_SECURE, "
        "'SECURE_SSL_REDIRECT': s.SECURE_SSL_REDIRECT, "
        "'SECURE_HSTS_SECONDS': s.SECURE_HSTS_SECONDS, "
        "'MIDDLEWARE': s.MIDDLEWARE, "
        "'EMAIL_SUBJECT_PREFIX': s.EMAIL_SUBJECT_PREFIX, "
        "'SERVERS': s.SPECTACULAR_SETTINGS['SERVERS']"
        "}))"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=settings.BASE_DIR,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


@pytest.fixture(scope="module", params=DEPLOYED_SETTINGS_MODULES)
def deployed_settings(request):
    return _load_deployed_settings(request.param)


def test_session_cookie_is_secure(deployed_settings):
    assert deployed_settings["SESSION_COOKIE_SECURE"] is True


def test_ssl_redirect_off_by_default(deployed_settings):
    # Traefik overwrites X-Forwarded-Proto with "http", so redirecting to https would loop forever.
    assert deployed_settings["SECURE_SSL_REDIRECT"] is False


def test_allow_cidr_middleware_runs_after_sessions(deployed_settings):
    middleware = deployed_settings["MIDDLEWARE"]
    session_index = middleware.index("django.contrib.sessions.middleware.SessionMiddleware")
    assert middleware[session_index + 1] == "allow_cidr.middleware.AllowCIDRMiddleware"


@pytest.mark.parametrize(
    "module, url",
    [
        ("config.settings.staging", "https://connect-staging.dimagi.com"),
        ("config.settings.production", "https://connect.dimagi.com"),
    ],
)
def test_api_servers_point_at_own_domain(module, url):
    assert [server["url"] for server in _load_deployed_settings(module)["SERVERS"]] == [url]


def test_production_debug_ignores_environment(monkeypatch):
    monkeypatch.setenv("DJANGO_DEBUG", "True")
    assert _load_deployed_settings("config.settings.production")["DEBUG"] is False


@pytest.mark.parametrize(
    "module, max_age",
    [
        ("config.settings.staging", 60),
        ("config.settings.production", 86400),
    ],
)
def test_default_hsts_max_age(monkeypatch, module, max_age):
    monkeypatch.delenv("DJANGO_SECURE_HSTS_SECONDS", raising=False)
    assert _load_deployed_settings(module)["SECURE_HSTS_SECONDS"] == max_age


@pytest.mark.parametrize(
    "module, prefix",
    [
        ("config.settings.staging", "[Connect Staging] "),
        ("config.settings.production", "[Connect]"),
    ],
)
def test_default_email_subject_prefix(monkeypatch, module, prefix):
    monkeypatch.delenv("DJANGO_EMAIL_SUBJECT_PREFIX", raising=False)
    assert _load_deployed_settings(module)["EMAIL_SUBJECT_PREFIX"] == prefix


@pytest.mark.parametrize(
    "accept, expected_status",
    [
        ("text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8", 401),  # browser
        ("application/json; version=1.0", 401),  # mobile app
        (None, 401),
        # The one client that changes behaviour: nothing it accepts can be rendered any more.
        ("text/html", 406),
    ],
)
def test_api_responds_with_json_only(client, accept, expected_status):
    headers = {"accept": accept} if accept else {}

    response = client.get("/api/users/", headers=headers)

    assert response.status_code == expected_status
    assert response["Content-Type"] == "application/json"


def test_collectstatic_leaves_out_unused_third_party_static(tmp_path):
    with override_settings(STATIC_ROOT=tmp_path):
        call_command("collectstatic", interactive=False, verbosity=0)

    collected = {p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file()}
    assert not [p for p in collected if p.startswith(("rest_framework/", "django_tables2/"))]
    # The ignore patterns must not reach static we do serve.
    assert {"admin/css/base.css", "js/htmx.js"} <= collected
