import os

import sentry_sdk
from celery import Celery
from celery.signals import task_retry

# set the default Django settings module for the 'celery' program.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")

app = Celery("commcare_connect")

# Using a string here means the worker doesn't have to serialize
# the configuration object to child processes.
# - namespace='CELERY' means all celery-related configuration keys
#   should have a `CELERY_` prefix.
app.config_from_object("django.conf:settings", namespace="CELERY")

# Load task modules from all registered Django app configs.
app.autodiscover_tasks()


@task_retry.connect
def sentry_log_retry(request=None, reason=None, einfo=None, **kwargs):
    with sentry_sdk.push_scope() as scope:
        if request:
            scope.set_tag("celery_task", request.task)
            scope.set_tag("retries", request.retries)
        if reason:
            scope.set_extra("reason", str(reason))
        sentry_sdk.capture_message("Celery task retrying", level="warning")
