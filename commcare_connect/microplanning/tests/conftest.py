import pytest
from django.core.cache import cache


@pytest.fixture
def local_cache(settings):
    """Keep cache reads off the shared Redis, and out of each other's way."""
    settings.CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
    cache.clear()
    return cache
