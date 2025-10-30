import logging
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
from django.conf import settings

from config import celery_app

logger = logging.getLogger(__name__)


@dataclass
class Event:
    name: str
    params: dict[str, Any]


def send_event_to_ga(request, event: Event):
    send_bulk_events_to_ga(request, [event])


def send_bulk_events_to_ga(request, events: list[Event]):
    if not settings.GA_MEASUREMENT_ID:
        logger.info("Please specify GA_MEASUREMENT_ID environment variable.")
        return

    if not settings.GA_API_SECRET:
        logger.info("Please specify GA_API_SECRET environment variable.")
        return

    client_id = _get_ga_client_id(request)
    session_id = _get_ga_session_id(request)
    is_dimagi = request.user.email.endswith("@dimagi.com")
    for event in events:
        event.params.update(
            {
                "session_id": session_id,
                "isDimagi": is_dimagi,
                # This is needed for tracking to work properly.
                "engagement_time_msec": 100,
            }
        )
    send_event_task.delay(client_id, _serialize_events(events))


@celery_app.task()
def send_event_task(client_id: str, events: list[Event]):
    measurement_id = settings.GA_MEASUREMENT_ID
    ga_api_secret = settings.GA_API_SECRET
    base_url = "https://www.google-analytics.com/mp/collect"
    params = {"measurement_id": measurement_id, "api_secret": ga_api_secret}
    url = f"{base_url}?{urlencode(params)}"
    response = httpx.post(url, json={"client_id": client_id, "events": events})
    response.raise_for_status()
    return response


def _serialize_events(events: list[Event]):
    return [asdict(event) for event in events]


def _get_ga_client_id(request):
    measurement_id = settings.GA_MEASUREMENT_ID[2:]
    client_id = request.COOKIES.get(f"_ga_{measurement_id}")
    return client_id


def _get_ga_session_id(request):
    session_id_cookie = request.COOKIES.get("_ga")
    if session_id_cookie:
        return session_id_cookie.split(".")[2]
    return
