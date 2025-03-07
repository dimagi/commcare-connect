import httpx
from django.conf import settings
from httpx import BasicAuth, Response

from commcare_connect.cache import quickcache
from commcare_connect.connect_id_client.models import (
    ConnectIdUser,
    Credential,
    DemoUser,
    Message,
    MessagingBulkResponse,
    MessagingResponse,
)
from commcare_connect.organization.models import Organization

GET = "GET"
POST = "POST"


def fetch_users(phone_number_list: list[str]) -> list[ConnectIdUser]:
    response = _make_request(GET, "/users/fetch_users", params={"phone_numbers": phone_number_list})
    data = response.json()
    return [ConnectIdUser(**user_dict) for user_dict in data["found_users"]]


def fetch_demo_user_tokens() -> list[DemoUser]:
    response = _make_request(GET, "/users/demo_users")
    data = response.json()
    return [DemoUser(**user_dict) for user_dict in data["demo_users"]]


def send_message(message: Message):
    """Send a push notification to a user."""
    response = _make_request(POST, "/messaging/send/", json=message.asdict())
    data = response.json()
    return MessagingResponse.build(**data)


def send_message_bulk(messages: list[Message]) -> MessagingBulkResponse:
    """Send a push notification to multiple users."""
    json = {"messages": [message.asdict() for message in messages]}
    response = _make_request(POST, "/messaging/send_bulk/", json=json, timeout=30)
    data = response.json()
    return MessagingBulkResponse.build(**data)


def add_credential(organization: Organization, credential: str, users: list[str]):
    json = {
        "users": users,
        "organization": organization.slug,
        "organization_name": organization.name,
        "credential": credential,
    }
    _make_request(POST, "/users/add_credential", json=json, timeout=30)
    return


def fetch_credentials(org_slug=None):
    params = {}
    if org_slug:
        params["org_slug"] = org_slug
    response = _make_request(GET, "/users/fetch_credentials", params=params)
    data = response.json()
    return [Credential(**c) for c in data["credentials"]]


def filter_users(country_code: str, credential: list[str]):
    params = {"country": country_code, "credential": credential}
    response = _make_request(GET, "/users/filter_users", params=params)
    data = response.json()
    return [ConnectIdUser(**user_dict) for user_dict in data["found_users"]]


@quickcache(vary_on=[], timeout=60 * 60)
def fetch_user_counts() -> dict[str, int]:
    response = _make_request(GET, "/users/fetch_user_counts")
    data = response.json()
    return data


def _make_request(method, path, params=None, json=None, timeout=5) -> Response:
    if json and not method == "POST":
        raise ValueError("json can only be used with POST requests")

    auth = BasicAuth(settings.CONNECTID_CLIENT_ID, settings.CONNECTID_CLIENT_SECRET)
    response = httpx.request(
        method, f"{settings.CONNECTID_URL}{path}", params=params, json=json, auth=auth, timeout=timeout
    )
    response.raise_for_status()
    return response
