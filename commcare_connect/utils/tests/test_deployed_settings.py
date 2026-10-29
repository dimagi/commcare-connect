import subprocess
import sys

import pytest
from django.conf import settings
from django.core.management import call_command
from django.test import override_settings


def test_session_cookie_is_secure():
    # Imported in a subprocess: staging.py mutates base.py's MIDDLEWARE list in place, which would alter the
    # settings of the running test session.
    result = subprocess.run(
        [sys.executable, "-c", "import config.settings.staging as s; print(s.SESSION_COOKIE_SECURE)"],
        cwd=settings.BASE_DIR,
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout.strip() == "True"


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
