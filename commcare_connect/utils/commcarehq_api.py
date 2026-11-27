import asyncio
import re

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
HQ_ACCESS_TIMEOUT = 50
# HQ project space names (its legacy_domain_re): letters, digits, "_", "-", "." and ":", so never "/" or "?".
HQ_DOMAIN_RE = re.compile(r"[\w.:-]+", re.ASCII)


def is_valid_hq_domain_name(domain: str) -> bool:
    """Whether the text is shaped like an HQ project space name, and so is safe to put in an HQ URL path. It
    doesn't ask HQ whether the project space exists."""
    return bool(HQ_DOMAIN_RE.fullmatch(domain)) and bool(domain.strip("."))


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


def check_api_key_access(api_key: HQApiKey, paths) -> list[Exception | None]:
    """Ask HQ, concurrently, whether `api_key` may GET each of `paths`, such as `app_path` and
    `mobile_workers_path`.

    Returns one entry per path, in order: None when HQ answers with a 2xx, otherwise the exception
    (CommCareHQAccessDenied when HQ refused the key). A check that fails unexpectedly, such as on a malformed
    server URL, returns its exception rather than raising, so it can't take down the other checks."""
    return _check_access(api_key.hq_server.url, _auth_header(api_key), list(paths))


def app_path(cc_domain, cc_app_id):
    return f"/a/{cc_domain}/api/v0.5/application/{cc_app_id}/"


def mobile_workers_path(cc_domain):
    """One user of the domain's mobile worker list. HQ guards listing and creating mobile workers with the same
    permissions, "Edit Mobile Workers" plus "Access APIs", so a key that can read it can also create the mobile
    worker every new Connect Worker needs to start learning."""
    return f"/a/{cc_domain}/api/v0.5/user/?limit=1"


def _fetch_app(api_key, cc_domain, cc_app_id, timeout) -> httpx.Response:
    try:
        response = httpx.get(
            f"{api_key.hq_server.url}{app_path(cc_domain, cc_app_id)}",
            headers={"Authorization": _auth_header(api_key)},
            timeout=timeout,
        )
        response.raise_for_status()
    except httpx.HTTPStatusError:
        raise _response_error(response, "Failed to fetch app structure")
    except httpx.RequestError as e:
        raise CommCareHQAPIException(f"Failed to fetch app structure: {e}")
    return response


@async_to_sync
async def _check_access(hq_server_url, auth_header, paths):
    async with httpx.AsyncClient(headers={"Authorization": auth_header}, timeout=HQ_ACCESS_TIMEOUT) as client:
        return await asyncio.gather(
            *(_check_path(client, hq_server_url, path) for path in paths),
            return_exceptions=True,
        )


async def _check_path(client, hq_server_url, path):
    failure = f"Failed to check access to {path}"
    try:
        # httpx's timeout applies to each phase (connect, then each read) separately, so it alone can't stop
        # a slow server from running the check past the deadline. This bounds the whole check.
        async with asyncio.timeout(HQ_ACCESS_TIMEOUT):
            # Streamed, so a successful check reads only the status, not the whole resource.
            async with client.stream("GET", f"{hq_server_url}{path}") as response:
                if response.is_success:
                    return None
                await response.aread()
                return _response_error(response, failure)
    except httpx.RequestError as e:
        return CommCareHQAPIException(f"{failure}: {e}")
    except TimeoutError:
        return CommCareHQAPIException(f"{failure}: no answer within {HQ_ACCESS_TIMEOUT}s")


def _response_error(response, failure) -> CommCareHQAPIException:
    message = f"{failure} ({response.status_code}): {response.text}"
    if response.status_code in ACCESS_DENIED_STATUS_CODES:
        return CommCareHQAccessDenied(message)
    return CommCareHQAPIException(message)


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
