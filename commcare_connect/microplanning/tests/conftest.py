import pytest

from commcare_connect.microplanning.models import OvertureRelease

SOME_RELEASE = "2026-08-19.0"


@pytest.fixture
def release(db):
    """Put the recorded Overture release in a given state, or in none of them when given None."""

    def _set(value):
        OvertureRelease.objects.all().delete()
        if value is not None:
            OvertureRelease.set_current(value)

    return _set


@pytest.fixture
def overture_release(release):
    """A release is recorded, as the daily task would have recorded it."""
    release(SOME_RELEASE)
    return SOME_RELEASE
