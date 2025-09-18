from typing import Any

import httpx
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def send_event_to_ga(request, event_name: str, params: dict[str, Any]):
    measurement_id = settings.GA_MEASUREMENT_ID
    ga_api_secret = settings.GA_API_SECRET

    if not measurement_id or not ga_api_secret:
        raise ImproperlyConfigured("Missing GA_MEASUREMENT_ID or GA_API_SECRET environment variables.")

    url = f"https://www.google-analytics.com/mp/collect?measurement_id={measurement_id}&api_secret={ga_api_secret}"
    client_id = _get_ga_client_id(request)
    session_id = _get_ga_session_id(request)
    response = httpx.post(
        url,
        json={
            "client_id": client_id,
            "events": [
                {
                    "name": event_name,
                    "params": {
                        **params,
                        "session_id": session_id,
                    },
                },
            ],
        },
    )
    return response


def _get_ga_client_id(request):
    measurement_id = settings.GA_MEASUREMENT_ID[2:]
    client_id = request.COOKIES.get(f"_ga_{measurement_id}")
    return client_id


def _get_ga_session_id(request):
    session_id_cookie = request.COOKIES.get("_ga")
    _, _, session_id, _ = session_id_cookie.split(".")
    return session_id
