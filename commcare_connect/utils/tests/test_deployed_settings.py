import subprocess
import sys

import pytest
from django.conf import settings


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
