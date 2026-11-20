import asyncio

import httpx
from asgiref.sync import async_to_sync

from commcare_connect.opportunity.models import HQApiKey


class CommCareHQAPIException(Exception):
    pass


class CommCareTokenException(CommCareHQAPIException):
    pass


class CommCareHQAccessDenied(CommCareHQAPIException):
    """HQ refused the request because the API key cannot see the resource (401, 403 or 404)."""


ACCESS_DENIED_STATUS_CODES = (401, 403, 404)
# The domain and app lists load inside a web request, so they must answer before the proxies in front of Connect
# give up on it. Production has CloudFront in front of the load balancer, and its origin timeout defaults to 30s.
HQ_LIST_TIMEOUT = 25
# HQ can answer an error with a whole HTML page; keep enough of it to tell what went wrong.
ERROR_BODY_LIMIT = 500


def get_domains_for_user(api_key):
    action = "Failed to fetch domains"
    objects = _fetch_hq_objects(api_key, "/api/v0.5/user_domains/?limit=100", action)
    try:
        return [domain["domain_name"] for domain in objects]
    except (KeyError, TypeError) as e:
        raise CommCareHQAPIException(f"{action}: unexpected domain in HQ's answer ({e!r})")


def get_applications_for_user_by_domain(api_key: HQApiKey, domain):
    action = "Failed to fetch applications"
    objects = _fetch_hq_objects(api_key, f"/a/{domain}/api/v0.5/application/", action)
    try:
        return [_application_option(application) for application in objects]
    except AttributeError as e:
        raise CommCareHQAPIException(f"{action}: unexpected application in HQ's answer ({e!r})")


def get_app_structure(api_key, app):
    """Fetch the full application structure JSON from CommCare HQ."""
    try:
        response = httpx.get(
            f"{api_key.hq_server.url}/a/{app.cc_domain}/api/v0.5/application/{app.cc_app_id}/",
            headers={"Authorization": f"ApiKey {api_key.user.email}:{api_key.api_key}"},
            timeout=300,
        )
        response.raise_for_status()
    except httpx.HTTPStatusError:
        raise CommCareHQAPIException(f"Failed to fetch app structure: {response.text}")
    except httpx.RequestError as e:
        raise CommCareHQAPIException(f"Failed to fetch app structure: {e}")
    return response.json()


def _fetch_hq_objects(api_key, path, action):
    """GET an HQ list endpoint and return its `objects`. Every failure raises CommCareHQAPIException, including an
    answer that isn't the list HQ's API gives, such as a maintenance page served with a 200."""
    response = _get_within_deadline(
        api_key.hq_server.url, f"ApiKey {api_key.user.email}:{api_key.api_key}", path, action
    )
    if not response.is_success:
        raise _response_error(response, action)
    try:
        objects = response.json()["objects"]
    except (ValueError, KeyError, TypeError):
        raise CommCareHQAPIException(f"{action}: HQ's answer has no list of objects: {_error_body(response)}")
    if not isinstance(objects, list):
        raise CommCareHQAPIException(f"{action}: HQ's answer has no list of objects: {_error_body(response)}")
    return objects


@async_to_sync
async def _get_within_deadline(hq_server_url, auth_header, path, action):
    async with httpx.AsyncClient(
        timeout=HQ_LIST_TIMEOUT, headers={"Authorization": auth_header}, base_url=hq_server_url
    ) as client:
        try:
            # httpx's timeout applies to each phase (connect, then each read) separately, so a slow server that
            # keeps trickling data in would outlast the proxies. This bounds the whole request.
            async with asyncio.timeout(HQ_LIST_TIMEOUT):
                return await client.get(path)
        except httpx.RequestError as e:
            raise CommCareHQAPIException(f"{action}: {e}")
        except TimeoutError:
            raise CommCareHQAPIException(f"{action}: no answer within {HQ_LIST_TIMEOUT}s")


def _application_option(application):
    app_name = application.get("name")
    # The top-level app object is always a draft (is_released=False).
    # Released builds are nested inside 'versions', so we check there.
    versions = application.get("versions", [])
    is_released = any(v.get("is_released") for v in versions)
    if not is_released:
        app_name = f"Unreleased - {app_name}"
    return {"id": application.get("id"), "name": app_name}


def _response_error(response, action) -> CommCareHQAPIException:
    message = f"{action} ({response.status_code}): {_error_body(response)}"
    if response.status_code in ACCESS_DENIED_STATUS_CODES:
        return CommCareHQAccessDenied(message)
    return CommCareHQAPIException(message)


def _error_body(response):
    return response.text[:ERROR_BODY_LIMIT]
