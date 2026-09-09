import pytest
from django.core.cache import cache

from commcare_connect.microplanning.models import OvertureRelease


@pytest.fixture
def local_cache(settings):
    """Keep cache reads off the shared Redis, and out of each other's way."""
    settings.CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
    cache.clear()
    return cache


@pytest.fixture
def overture_release(db):
    OvertureRelease.set_current("2026-08-19.0")
    return OvertureRelease.current()
