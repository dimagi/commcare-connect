import subprocess
import sys

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
