import json
import subprocess
import sys

import pytest
from django.conf import settings

DEPLOYED_SETTINGS_MODULES = ["config.settings.staging", "config.settings.production"]


def _load_deployed_settings(module):
    # Imported in a subprocess: deployed.py mutates base.py's MIDDLEWARE list in place, which would alter the
    # settings of the running test session.
    script = (
        f"import json, {module} as s; "
        "print(json.dumps({"
        "'SESSION_COOKIE_SECURE': s.SESSION_COOKIE_SECURE, "
        "'SECURE_SSL_REDIRECT': s.SECURE_SSL_REDIRECT, "
        "'MIDDLEWARE': s.MIDDLEWARE, "
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
