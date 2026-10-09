import json

import httpx
import pytest
from django.core.cache import cache

from .client import (
    TOKEN_CACHE_KEY,
    TOKEN_URL,
    CustomerRequest,
    JiraServiceDeskNotConfigured,
    attach_temporary_file,
    create_request,
    is_configured,
)

CLOUD_ID = "test-cloud"
API_URL = f"https://api.atlassian.com/ex/jira/{CLOUD_ID}/rest/servicedeskapi"


@pytest.fixture(autouse=True)
def jira_settings(settings):
    # Set explicitly: a developer's .env may hold real credentials, and it leaks into test settings.
    settings.JIRA_SERVICE_DESK_CLIENT_ID = "client-id"
    settings.JIRA_SERVICE_DESK_CLIENT_SECRET = "client-secret"
    settings.JIRA_SERVICE_DESK_CLOUD_ID = CLOUD_ID
    cache.delete(TOKEN_CACHE_KEY)
    yield settings
    cache.delete(TOKEN_CACHE_KEY)


def add_token_response(httpx_mock, token="token-1"):
    httpx_mock.add_response(
        method="POST", url=TOKEN_URL, json={"access_token": token, "expires_in": 3600, "token_type": "Bearer"}
    )


@pytest.mark.parametrize(
    "client_id, client_secret, expected",
    [("id", "secret", True), (None, "secret", False), ("id", None, False), ("", "", False)],
)
def test_is_configured(jira_settings, client_id, client_secret, expected):
    jira_settings.JIRA_SERVICE_DESK_CLIENT_ID = client_id
    jira_settings.JIRA_SERVICE_DESK_CLIENT_SECRET = client_secret
    assert is_configured() is expected


def test_create_request(httpx_mock):
    add_token_response(httpx_mock)
    httpx_mock.add_response(
        method="POST",
        url=f"{API_URL}/request",
        json={"issueKey": "FIN-1", "_links": {"web": "https://example.atlassian.net/servicedesk/customer/FIN-1"}},
    )

    result = create_request("19", "186", {"summary": "Pay"}, {"2": {"choices": ["3"]}})

    assert result == CustomerRequest(key="FIN-1", url="https://example.atlassian.net/servicedesk/customer/FIN-1")
    token_request, create = httpx_mock.get_requests()
    assert dict(httpx.QueryParams(token_request.content.decode())) == {
        "grant_type": "client_credentials",
        "client_id": "client-id",
        "client_secret": "client-secret",
    }
    assert create.headers["Authorization"] == "Bearer token-1"
    assert json.loads(create.content) == {
        "serviceDeskId": "19",
        "requestTypeId": "186",
        "requestFieldValues": {"summary": "Pay"},
        "form": {"answers": {"2": {"choices": ["3"]}}},
    }


def test_attach_temporary_file(httpx_mock):
    add_token_response(httpx_mock)
    httpx_mock.add_response(
        method="POST",
        url=f"{API_URL}/servicedesk/19/attachTemporaryFile",
        json={"temporaryAttachments": [{"temporaryAttachmentId": "temp-1", "fileName": "invoice.pdf"}]},
    )

    assert attach_temporary_file("19", "invoice.pdf", b"%PDF", "application/pdf") == "temp-1"
    upload = httpx_mock.get_requests()[-1]
    assert upload.headers["X-Atlassian-Token"] == "no-check"
    assert b'filename="invoice.pdf"' in upload.content


def test_token_is_reused_until_it_expires(httpx_mock):
    add_token_response(httpx_mock)
    httpx_mock.add_response(
        method="POST", url=f"{API_URL}/request", json={"issueKey": "FIN-1", "_links": {"web": "u"}}
    )
    httpx_mock.add_response(
        method="POST", url=f"{API_URL}/request", json={"issueKey": "FIN-2", "_links": {"web": "u"}}
    )

    create_request("19", "186", {}, {})
    create_request("19", "186", {}, {})

    assert [request.url for request in httpx_mock.get_requests()].count(TOKEN_URL) == 1


def test_unauthorized_response_drops_cached_token(httpx_mock):
    add_token_response(httpx_mock)
    httpx_mock.add_response(method="POST", url=f"{API_URL}/request", status_code=401)

    with pytest.raises(httpx.HTTPStatusError):
        create_request("19", "186", {}, {})

    assert cache.get(TOKEN_CACHE_KEY) is None


def test_unconfigured_raises_before_any_call(jira_settings, httpx_mock):
    jira_settings.JIRA_SERVICE_DESK_CLIENT_ID = None

    with pytest.raises(JiraServiceDeskNotConfigured):
        create_request("19", "186", {}, {})

    assert httpx_mock.get_requests() == []


class TestDryRun:
    @pytest.fixture(autouse=True)
    def no_credentials(self, jira_settings):
        jira_settings.JIRA_SERVICE_DESK_CLIENT_ID = None
        jira_settings.JIRA_SERVICE_DESK_CLIENT_SECRET = None
        return jira_settings

    @pytest.mark.parametrize("value", ["true", "success", "1"])
    def test_succeeds_without_calling_jira(self, jira_settings, httpx_mock, value):
        jira_settings.JIRA_SERVICE_DESK_DRY_RUN = value

        assert is_configured()
        assert attach_temporary_file("19", "invoice.pdf", b"%PDF", "application/pdf").startswith("dry-run-")
        result = create_request("19", "186", {"summary": "Pay"}, {})

        assert result.key.startswith("DRY-RUN-")
        assert httpx_mock.get_requests() == []

    @pytest.mark.parametrize(
        "mode, error",
        [("reject", httpx.HTTPStatusError), ("timeout", httpx.ReadTimeout), ("unreachable", httpx.ConnectError)],
    )
    def test_simulated_failures(self, jira_settings, httpx_mock, mode, error):
        jira_settings.JIRA_SERVICE_DESK_DRY_RUN = mode

        with pytest.raises(error):
            create_request("19", "186", {}, {})

        assert httpx_mock.get_requests() == []

    def test_unknown_mode_is_an_error(self, jira_settings):
        jira_settings.JIRA_SERVICE_DESK_DRY_RUN = "maybe"

        with pytest.raises(ValueError):
            is_configured()

    def test_off_by_default(self):
        assert not is_configured()
