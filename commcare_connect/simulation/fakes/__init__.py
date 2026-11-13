"""Stand-ins for the services a local environment cannot reach: CommCare HQ, ConnectID, Twilio and analytics.

Each area module exposes ``patches()``, a list of ``unittest.mock`` patchers. ``local_fakes()`` starts all of
them for the duration of a simulation run. Patch the name where the code under simulation looks it up, as the
existing tests do, so the real code path runs right up to the outbound call.
"""

from contextlib import ExitStack, contextmanager
from unittest import mock

from config.celery_app import app as celery_app

from . import payments, review, setup, workers

AREAS = (setup, workers, review, payments)
# Pages the steps submit report events to Google Analytics when it is configured; simulated use is not real use.
ANALYTICS_TASKS = (
    "commcare_connect.utils.analytics.send_event_task",
    "commcare_connect.opportunity.tasks.send_event_task",
)


@contextmanager
def local_fakes():
    with ExitStack() as stack:
        stack.enter_context(_eager_celery())
        for target in ANALYTICS_TASKS:
            stack.enter_context(mock.patch(target))
        for area in AREAS:
            for patcher in area.patches():
                stack.enter_context(patcher)
        yield


@contextmanager
def _eager_celery():
    """Run tasks inline so each step's side effects exist before the next step starts."""
    previous = celery_app.conf.task_always_eager
    celery_app.conf.task_always_eager = True
    try:
        yield
    finally:
        celery_app.conf.task_always_eager = previous
