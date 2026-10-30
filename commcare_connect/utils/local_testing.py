"""Local-only test mode, switched on by ENABLE_TEST_SUBMISSIONS (with DEBUG).

Kept free of app imports so anything, including Celery tasks, can check the gate without import cycles.
"""

from django.conf import settings
from django.http import Http404


def simulator_enabled() -> bool:
    return bool(settings.DEBUG and settings.ENABLE_TEST_SUBMISSIONS)


def ensure_simulator_enabled() -> None:
    if not simulator_enabled():
        raise Http404("Test submissions are not enabled")


class SimulatorEnabledMixin:
    """For the Test submissions views: 404 unless test mode is on, before any other check."""

    def dispatch(self, request, *args, **kwargs):
        ensure_simulator_enabled()
        return super().dispatch(request, *args, **kwargs)
