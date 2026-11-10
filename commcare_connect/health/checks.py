"""Health checks served at /status/ (see commcare_connect.health.views.HealthCheck).

Each subset is the list of checks one /status/ URL runs:

- ``general`` (``/status/``): the web process's own dependencies: the database (and the secondary
  database when one is configured), the cache and the Celery broker. It leaves out Celery workers so
  that the web tier is not reported down because workers are.
- ``celery`` (``/status/celery/``): at least one Celery worker answers a ping and every configured
  queue has a worker consuming it.

A check is a ``health_check`` check path, optionally as a ``(path, kwargs)`` tuple, or a subclass of
``health_check.base.HealthCheck`` that raises ``ServiceUnavailable`` from ``run()``.
"""

import dataclasses
import datetime

import celery
from celery.app import app_or_default
from django.conf import settings
from health_check.contrib.celery import Ping
from redis.asyncio import Redis as RedisClient

# Celery's broadcast ping reports a slow reply the same as no reply, so leave headroom for a busy
# broker, for example while a deploy restarts the workers.
CELERY_PING_TIMEOUT = datetime.timedelta(seconds=3)


@dataclasses.dataclass
class CeleryPing(Ping):
    """The library's Celery check, without the app's memory address in its name on the status page."""

    app: celery.Celery = dataclasses.field(default_factory=app_or_default, repr=False)


def get_check_subsets():
    return {
        "general": [
            *_database_checks(),
            "health_check.checks.Cache",
            ("health_check.contrib.redis.Redis", {"client_factory": _broker_client}),
        ],
        "celery": [
            (CeleryPing, {"timeout": CELERY_PING_TIMEOUT}),
        ],
    }


def _database_checks():
    return [("health_check.checks.Database", {"alias": alias}) for alias in settings.DATABASES]


def _broker_client():
    return RedisClient.from_url(settings.CELERY_BROKER_URL)
