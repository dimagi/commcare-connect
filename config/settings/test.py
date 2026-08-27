"""
With these settings, tests run faster.
"""

import platform
from urllib.parse import urlsplit, urlunsplit

from .base import *  # noqa
from .base import env

if platform.system() == "Darwin":
    GDAL_LIBRARY_PATH = env("GDAL_LIBRARY_PATH")
    GEOS_LIBRARY_PATH = env("GEOS_LIBRARY_PATH")

# GENERAL
# ------------------------------------------------------------------------------
SECRET_KEY = env(
    "DJANGO_SECRET_KEY",
    default="G11wtQ0L0YWp13SJhMLlKlFrCsTuNm6s5Q6Q2o0U2E75hf0kRoV5hiK86yye0Tar",
)
TEST_RUNNER = "django.test.runner.DiscoverRunner"

# CACHES
# ------------------------------------------------------------------------------
# Give tests their own Redis database. Sharing one with a local dev server leaked waffle
# switch state both ways. A switch toggled in the dev admin was cached and then read back by
# the test suite, failing tests that expect it off. In the other direction, a test reading a
# switch that does not exist in the test database makes waffle create it as inactive
# (WAFFLE_CREATE_MISSING_SWITCHES) and cache False; the flush that would invalidate that is
# scheduled via transaction.on_commit, which never runs because the test transaction is
# rolled back, so the dev server goes on reading the orphaned False.
#
# Only the database index changes. The django-redis backend is kept because code under test
# calls cache.lock(), which is a django-redis extension that LocMemCache does not provide.
TEST_REDIS_DB = env.int("TEST_REDIS_DB", default=15)
CACHES["default"]["LOCATION"] = urlunsplit(  # noqa: F405
    urlsplit(CACHES["default"]["LOCATION"])._replace(path=f"/{TEST_REDIS_DB}")  # noqa: F405
)

# PASSWORDS
# ------------------------------------------------------------------------------
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# DEBUGGING FOR TEMPLATES
# ------------------------------------------------------------------------------
TEMPLATES[0]["OPTIONS"]["debug"] = True  # type: ignore # noqa: F405

STORAGES["staticfiles"]["BACKEND"] = "django.contrib.staticfiles.storage.StaticFilesStorage"  # noqa: F405

# CommCareConnect
# ------------------------------------------------------------------------------
