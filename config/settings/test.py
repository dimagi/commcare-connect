"""
With these settings, tests run faster.
"""

import platform
from urllib.parse import urlsplit, urlunsplit

from django.core.exceptions import ImproperlyConfigured

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
# Give tests their own Redis database. Sharing one with a local dev server leaks cached state
# both ways.
# The values a test writes outlives it because the invalidation that would clear
# the cache is typically scheduled via transaction.on_commit, which never runs when the test
# transaction is rolled back.
#
# Only the database index changes. The django-redis backend is kept because code under test
# calls cache.lock(), which is a django-redis extension that LocMemCache does not provide.
#
# pytest-xdist workers (gw0, gw1, ...) each count down from TEST_REDIS_DB to a database of their own,
# because tests call cache.clear(), which flushes the whole database and not just one worker's keys.
# Redis has 16 databases by default, so `-n` beyond TEST_REDIS_DB + 1 workers fails at startup.
XDIST_WORKER_NUMBER = int(env.str("PYTEST_XDIST_WORKER", default="gw0").removeprefix("gw"))
TEST_REDIS_DB = env.int("TEST_REDIS_DB", default=15) - XDIST_WORKER_NUMBER
if TEST_REDIS_DB < 0:
    raise ImproperlyConfigured(f"No Redis database left for xdist worker {XDIST_WORKER_NUMBER}; run fewer workers.")
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
# Not in base: it imports dev-only packages. Installed here so CI runs every simulation scenario.
INSTALLED_APPS += ["commcare_connect.simulation"]  # noqa: F405
