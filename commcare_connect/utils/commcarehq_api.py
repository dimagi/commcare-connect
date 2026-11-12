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
# The access check runs inside a web request, so it must answer before the load balancer gives up on it.
APP_ACCESS_TIMEOUT = 50


def get_domains_for_user(api_key):
    response = httpx.get(
        f"{api_key.hq_server.url}/api/v0.5/user_domains/?limit=100",
        headers={"Authorization": f"ApiKey {api_key.user.email}:{api_key.api_key}"},
    )
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError:
        raise CommCareHQAPIException(f"Failed to fetch domains: {response.text}")
    data = response.json()
    domains = [domain["domain_name"] for domain in data["objects"]]
    return domains


def get_applications_for_user_by_domain(api_key: HQApiKey, domain):
    user_email = api_key.user.email
    hq_server_url = api_key.hq_server.url
    api_key = api_key.api_key
    return _get_applications_for_domain(user_email, api_key, domain, hq_server_url)


@async_to_sync
async def _get_applications_for_domain(user_email, api_key, domain, hq_server_url):
    async with httpx.AsyncClient(
        timeout=300,
        headers={
            "Authorization": f"ApiKey {user_email}:{api_key}",
        },
        base_url=hq_server_url,
    ) as client:
        applications = await _get_commcare_app_json(client, domain)
    return applications


def get_app_structure(api_key, app):
    """Fetch the full application structure JSON from CommCare HQ."""
    return _fetch_app(api_key, app.cc_domain, app.cc_app_id, timeout=300).json()


def app_access_errors(api_key: HQApiKey, apps) -> list[CommCareHQAPIException | None]:
    """Ask HQ, concurrently, whether `api_key` can fetch each `(cc_domain, cc_app_id)` in `apps`.

    Returns one entry per app, in order: None when the key can fetch it, otherwise the exception
    (CommCareHQAccessDenied when HQ refused the key)."""
    return _app_access_errors(api_key.hq_server.url, _auth_header(api_key), list(apps))


def _fetch_app(api_key, cc_domain, cc_app_id, timeout) -> httpx.Response:
    try:
        response = httpx.get(
            f"{api_key.hq_server.url}{_app_path(cc_domain, cc_app_id)}",
            headers={"Authorization": _auth_header(api_key)},
            timeout=timeout,
        )
        response.raise_for_status()
    except httpx.HTTPStatusError:
        raise _app_fetch_error(response)
    except httpx.RequestError as e:
        raise CommCareHQAPIException(f"Failed to fetch app structure: {e}")
    return response


@async_to_sync
async def _app_access_errors(hq_server_url, auth_header, apps):
    async with httpx.AsyncClient(headers={"Authorization": auth_header}, timeout=APP_ACCESS_TIMEOUT) as client:
        return await asyncio.gather(
            *(_app_access_error(client, f"{hq_server_url}{_app_path(domain, app_id)}") for domain, app_id in apps)
        )


async def _app_access_error(client, url):
    try:
        # httpx's timeout applies to each phase (connect, then each read) separately, so it alone can't stop
        # a slow server from running the check past the deadline. This bounds the whole check.
        async with asyncio.timeout(APP_ACCESS_TIMEOUT):
            # Streamed, so a successful check reads only the status, not the whole app.
            async with client.stream("GET", url) as response:
                if response.is_success:
                    return None
                await response.aread()
                return _app_fetch_error(response)
    except httpx.RequestError as e:
        return CommCareHQAPIException(f"Failed to fetch app structure: {e}")
    except TimeoutError:
        return CommCareHQAPIException(f"Failed to fetch app structure: no answer within {APP_ACCESS_TIMEOUT}s")


def _app_fetch_error(response) -> CommCareHQAPIException:
    message = f"Failed to fetch app structure ({response.status_code}): {response.text}"
    if response.status_code in ACCESS_DENIED_STATUS_CODES:
        return CommCareHQAccessDenied(message)
    return CommCareHQAPIException(message)


def _app_path(cc_domain, cc_app_id):
    return f"/a/{cc_domain}/api/v0.5/application/{cc_app_id}/"


def _auth_header(api_key):
    return f"ApiKey {api_key.user.email}:{api_key.api_key}"


async def _get_commcare_app_json(client, domain):
    applications = []
    response = await client.get(f"/a/{domain}/api/v0.5/application/")
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError:
        raise CommCareHQAPIException(f"Failed to fetch applications: {response.text}")
    data = response.json()

    for application in data.get("objects", []):
        app_name = application.get("name")
        # The top-level app object is always a draft (is_released=False).
        # Released builds are nested inside 'versions', so we check there.
        versions = application.get("versions", [])
        is_released = any(v.get("is_released") for v in versions)
        if not is_released:
            app_name = f"Unreleased - {app_name}"
        applications.append({"id": application.get("id"), "name": app_name})
    return applications
