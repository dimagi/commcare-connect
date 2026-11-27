import logging

import httpx
from django.db.models import Case, When
from rest_framework.response import Response

from commcare_connect.opportunity.models import HQApiKey
from commcare_connect.organization.models import Organization
from commcare_connect.users.models import ConnectIDUserLink
from commcare_connect.utils.commcarehq_api import CommCareHQAPIException
from commcare_connect.utils.error_codes import ErrorCodes

logger = logging.getLogger(__name__)


# The organization the user last opened, for pages whose URL names none, such as the marketplace.
CURRENT_ORG_SESSION_KEY = "current_org_slug"


def get_organization_for_request(request, view_kwargs):
    """The organization the URL names; on other pages, the one the user last opened, or else their first."""
    if not request.user.is_authenticated:
        return

    org_slug = view_kwargs.get("org_slug", None)
    if org_slug:
        try:
            organization = Organization.objects.get(slug=org_slug)
        except Organization.DoesNotExist:
            return None
        remember_organization(request, organization)
        return organization

    memberships = request.user.memberships.select_related("organization")
    session = getattr(request, "session", None)  # API requests authenticated by token may have none
    remembered_slug = session.get(CURRENT_ORG_SESSION_KEY) if session is not None else None
    if remembered_slug:
        # Looked up among their memberships, since the user may have left it; one query either way.
        memberships = memberships.order_by(Case(When(organization__slug=remembered_slug, then=0), default=1), "pk")
    membership = memberships.first()
    return membership.organization if membership else None


def remember_organization(request, organization):
    """Makes `organization` the one used on pages whose URL names none, such as the marketplace."""
    session = getattr(request, "session", None)  # API requests authenticated by token may have none
    # Only written when it changes, so the session isn't saved on every request. Staff can open organizations
    # they don't belong to; those aren't remembered.
    if session is None or session.get(CURRENT_ORG_SESSION_KEY) == organization.slug:
        return
    if request.user.memberships.filter(organization=organization).exists():
        session[CURRENT_ORG_SESSION_KEY] = organization.slug


def hq_user_error_response(user, domain, opportunity) -> Response | None:
    """Give the user an HQ mobile worker in the domain if they have none yet, as the app's start learning and
    claim calls need. Returns the error response for the app when that fails, or None."""
    try:
        user_created = create_hq_user_and_link(user, domain, opportunity)
    except (CommCareHQAPIException, httpx.RequestError):
        # HQ refused (e.g. the opportunity's key lacks "Edit Mobile Workers") or couldn't be reached.
        logger.exception("Failed to create/link HQ user in %s for opportunity %s", domain, opportunity.id)
        return Response({"error_code": ErrorCodes.FAILED_USER_CREATE}, status=502)
    if not user_created:
        return Response({"error_code": ErrorCodes.FAILED_USER_CREATE}, status=400)
    return None


def create_hq_user_and_link(user, domain, opportunity):
    hq_server = opportunity.hq_server
    api_key = opportunity.api_key
    if not ConnectIDUserLink.objects.filter(user=user, domain=domain, hq_server=hq_server).exists():
        response = _create_hq_user(user, domain, api_key)
        if response is None:
            return False
        hq_user_uuid = response.json().get("id") if response.status_code == 201 else None
        cc_username = f"{user.username.lower()}@{domain}.commcarehq.org"
        ConnectIDUserLink.objects.create(
            commcare_username=cc_username,
            user=user,
            domain=domain,
            hq_server=hq_server,
            hq_user_uuid=hq_user_uuid,
        )
    return True


def fetch_hq_user_uuid(link: ConnectIDUserLink, api_key: HQApiKey) -> str | None:
    for obj in _iter_hq_users(api_key, link.domain):
        if obj.get("username") == link.commcare_username:
            return obj.get("id")
    return None


def fetch_hq_user_uuids(api_key: HQApiKey, domain: str) -> dict[str, str]:
    """Return a {commcare_username: hq_user_uuid} map for every user in the domain.

    The API key is an opportunity-level admin credential, so a single sweep lists all
    mobile workers in the domain and backfills them in one call.
    """
    return {obj["username"]: obj["id"] for obj in _iter_hq_users(api_key, domain) if obj.get("username")}


def _iter_hq_users(api_key: HQApiKey, domain: str):
    headers = {"Authorization": f"ApiKey {api_key.user.email}:{api_key.api_key}"}
    next_url = f"{api_key.hq_server.url}/a/{domain}/api/v0.5/user/?limit=200"
    while next_url:
        response = httpx.get(next_url, headers=headers, timeout=30)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise CommCareHQAPIException(
                f"{e.response.status_code} Error response {e.response.text} while fetching users for {domain}"
            )
        data = response.json()
        yield from data.get("objects", [])
        next_path = data.get("meta", {}).get("next")
        next_url = f"{api_key.hq_server.url}{next_path}" if next_path else None


def _create_hq_user(user, domain, api_key):
    mobile_worker_api_url = f"{api_key.hq_server.url}/a/{domain}/api/v0.5/user/"
    hq_request = httpx.post(
        mobile_worker_api_url,
        json=build_hq_user_payload(user),
        headers={"Authorization": f"ApiKey {api_key.user.email}:{api_key.api_key}"},
        timeout=10,
    )
    try:
        hq_request.raise_for_status()
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 400 and "already taken" in e.response.text:
            return e.response
        raise CommCareHQAPIException(
            f"{e.response.status_code} Error response {e.response.text} while creating user {user.username}"
        )

    return hq_request if hq_request.status_code == 201 else None


def build_hq_user_payload(user):
    payload = {
        "username": user.username,
        "connect_username": user.username,
    }
    if user.name:
        payload["first_name"] = user.name
    if user.phone_number:
        payload["default_phone_number"] = user.phone_number
    return payload
