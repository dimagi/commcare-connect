import httpx

from commcare_connect.organization.models import Organization
from commcare_connect.users.models import ConnectIDUserLink
from commcare_connect.utils.commcarehq_api import CommCareHQAPIException


def get_organization_for_request(request, view_kwargs):
    if not request.user.is_authenticated:
        return

    org_slug = view_kwargs.get("org_slug", None)
    if org_slug:
        try:
            return Organization.objects.get(slug=org_slug)
        except Organization.DoesNotExist:
            return None

    membership = request.user.memberships.select_related("organization").first()
    return membership.organization if membership else None


def create_hq_user_and_link(user, domain, opportunity):
    hq_server = opportunity.hq_server
    api_key = opportunity.api_key
    if not ConnectIDUserLink.objects.filter(user=user, domain=domain, hq_server=hq_server).exists():
        user_created = _create_hq_user(user, domain, api_key)
        if not user_created:
            return False
        cc_username = f"{user.username.lower()}@{domain}.commcarehq.org"
        ConnectIDUserLink.objects.create(commcare_username=cc_username, user=user, domain=domain, hq_server=hq_server)
    return True


def _create_hq_user(user, domain, api_key):
    mobile_worker_api_url = f"{api_key.hq_server.url}/a/{domain}/api/v0.5/user/"
    hq_request = httpx.post(
        mobile_worker_api_url,
        json={
            "username": user.username,
            "connect_username": user.username,
        },
        headers={"Authorization": f"ApiKey {api_key.user.email}:{api_key.api_key}"},
        timeout=10,
    )
    try:
        hq_request.raise_for_status()
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 400 and "already taken" in e.response.text:
            return True
        raise CommCareHQAPIException(
            f"{e.response.status_code} Error response {e.response.text} while creating user {user.username}"
        )

    return hq_request.status_code == 201
