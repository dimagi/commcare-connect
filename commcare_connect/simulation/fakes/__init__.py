"""Stand-ins for the services a local environment cannot reach: CommCare HQ, ConnectID, Twilio and analytics.

Each area module exposes ``patches()``, a list of ``unittest.mock`` patchers. ``local_fakes()`` starts all of
them for the duration of a simulation run. Patch the name where the code under simulation looks it up, as the
existing tests do, so the real code path runs right up to the outbound call.
"""

from contextlib import ExitStack, contextmanager
from unittest import mock

from django.test import override_settings

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


def _eager_celery():
    """Run tasks inline so each step's side effects exist before the next step starts.

    Override the Django setting Celery reads from rather than assigning to ``celery_app.conf``: since Celery 5.7
    a value assigned to ``conf`` keeps shadowing the settings after it is put back, so the override would outlive
    the run.
    """
    return override_settings(CELERY_TASK_ALWAYS_EAGER=True)
