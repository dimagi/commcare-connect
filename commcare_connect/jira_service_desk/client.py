"""Client for raising Jira Service Management customer requests as Connect's Atlassian service account.

The service account authenticates with an OAuth 2.0 client credential: the client ID and secret are
exchanged for a short-lived access token, and every API call goes through the Atlassian API gateway
rather than the site's own hostname.
"""

from dataclasses import dataclass

import httpx
from django.conf import settings
from django.core.cache import cache

from commcare_connect.jira_service_desk import dry_run

TOKEN_URL = "https://auth.atlassian.com/oauth/token"
API_BASE_URL = "https://api.atlassian.com/ex/jira/{cloud_id}/rest/servicedeskapi"
TOKEN_CACHE_KEY = "jira_service_desk:access_token"
# Drop the cached token a minute before Atlassian expires it, so a request never carries a token
# that runs out while it is in flight.
TOKEN_EXPIRY_MARGIN = 60


class JiraServiceDeskNotConfigured(Exception):
    pass


@dataclass(frozen=True)
class CustomerRequest:
    key: str
    url: str


def is_configured() -> bool:
    if dry_run.get_mode():
        return True
    return bool(settings.JIRA_SERVICE_DESK_CLIENT_ID and settings.JIRA_SERVICE_DESK_CLIENT_SECRET)


def attach_temporary_file(service_desk_id: str, filename: str, content: bytes, content_type: str) -> str:
    """Upload a file to the service desk and return its temporary attachment ID.

    Temporary attachments expire unless a request uses them, so an upload that is never used
    leaves nothing behind.
    """
    if dry_run.get_mode():
        return dry_run.attach_temporary_file(filename, content)
    response = _make_request(
        "POST",
        f"/servicedesk/{service_desk_id}/attachTemporaryFile",
        headers={"X-Atlassian-Token": "no-check"},
        files={"file": (filename, content, content_type)},
        timeout=60,
    )
    return response.json()["temporaryAttachments"][0]["temporaryAttachmentId"]


def create_request(
    service_desk_id: str, request_type_id: str, field_values: dict, form_answers: dict
) -> CustomerRequest:
    """Raise a customer request, answering the request type's Jira Form with `form_answers`."""
    payload = {
        "serviceDeskId": service_desk_id,
        "requestTypeId": request_type_id,
        "requestFieldValues": field_values,
        "form": {"answers": form_answers},
    }
    if mode := dry_run.get_mode():
        key, url = dry_run.create_request(mode, payload)
        return CustomerRequest(key=key, url=url)
    response = _make_request("POST", "/request", json=payload, timeout=30)
    data = response.json()
    return CustomerRequest(key=data["issueKey"], url=data["_links"]["web"])


def _make_request(method: str, path: str, headers: dict | None = None, timeout: int = 10, **kwargs) -> httpx.Response:
    url = API_BASE_URL.format(cloud_id=settings.JIRA_SERVICE_DESK_CLOUD_ID) + path
    headers = {"Authorization": f"Bearer {_get_access_token()}", "Accept": "application/json", **(headers or {})}
    response = httpx.request(method, url, headers=headers, timeout=timeout, **kwargs)
    if response.status_code == httpx.codes.UNAUTHORIZED:
        # A revoked or rotated credential leaves a dead token in the cache; forget it so the
        # next call fetches a fresh one.
        cache.delete(TOKEN_CACHE_KEY)
    response.raise_for_status()
    return response


def _get_access_token() -> str:
    if token := cache.get(TOKEN_CACHE_KEY):
        return token
    if not is_configured():
        raise JiraServiceDeskNotConfigured(
            "JIRA_SERVICE_DESK_CLIENT_ID and JIRA_SERVICE_DESK_CLIENT_SECRET are not set"
        )
    response = httpx.post(
        TOKEN_URL,
        data={
            "grant_type": "client_credentials",
            "client_id": settings.JIRA_SERVICE_DESK_CLIENT_ID,
            "client_secret": settings.JIRA_SERVICE_DESK_CLIENT_SECRET,
        },
        timeout=10,
    )
    response.raise_for_status()
    data = response.json()
    cache.set(TOKEN_CACHE_KEY, data["access_token"], timeout=max(data["expires_in"] - TOKEN_EXPIRY_MARGIN, 1))
    return data["access_token"]
