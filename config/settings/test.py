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
# Give tests their own Redis database. Sharing one with a local dev server leaks cached state
# both ways.
# The values a test writes outlives it because the invalidation that would clear
# the cache is typically scheduled via transaction.on_commit, which never runs when the test
# transaction is rolled back.
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
