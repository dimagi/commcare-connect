import asyncio

import httpx
import pytest

from commcare_connect.opportunity.tests.factories import HQApiKeyFactory
from commcare_connect.utils import commcarehq_api
from commcare_connect.utils.commcarehq_api import (
    CommCareHQAccessDenied,
    CommCareHQAPIException,
    app_access_errors,
    get_applications_for_user_by_domain,
    get_domains_for_user,
)

APP_URL = "{url}/a/dom/api/v0.5/application/{app_id}/"


@pytest.mark.django_db
class TestAppAccessErrors:
    @pytest.fixture
    def key(self):
        return HQApiKeyFactory()

    def _mock(self, httpx_mock, key, app_id, **response):
        httpx_mock.add_response(url=APP_URL.format(url=key.hq_server.url, app_id=app_id), **response)

    def test_none_for_each_app_the_key_can_fetch(self, httpx_mock, key):
        self._mock(httpx_mock, key, "app1", json={"id": "app1"})
        self._mock(httpx_mock, key, "app2", json={"id": "app2"})
        assert app_access_errors(key, [("dom", "app1"), ("dom", "app2")]) == [None, None]

    @pytest.mark.parametrize(
        "status, access_denied",
        [(401, True), (403, True), (404, True), (429, False), (500, False), (502, False), (503, False)],
    )
    def test_only_refusals_are_access_denied(self, httpx_mock, key, status, access_denied):
        self._mock(httpx_mock, key, "app1", json={})
        self._mock(httpx_mock, key, "app2", status_code=status)
        ok, error = app_access_errors(key, [("dom", "app1"), ("dom", "app2")])
        assert ok is None
        assert str(status) in str(error)
        assert isinstance(error, CommCareHQAccessDenied) is access_denied

    def test_transport_error_is_not_access_denied(self, httpx_mock, key):
        httpx_mock.add_exception(httpx.ConnectTimeout("timed out"))
        [error] = app_access_errors(key, [("dom", "app1")])
        assert error is not None
        assert not isinstance(error, CommCareHQAccessDenied)

    @pytest.mark.parametrize("url", ["http://[::1", "http://example.com:99999"])
    def test_a_malformed_server_url_is_returned_not_raised(self, key, url):
        """httpx raises these outside RequestError, so the check must not let them escape as a 500."""
        key.hq_server.url = url
        errors = app_access_errors(key, [("dom", "app1"), ("dom", "app2")])
        assert len(errors) == 2
        assert all(error is not None and not isinstance(error, CommCareHQAccessDenied) for error in errors)

    def test_a_check_that_outlasts_the_deadline_is_not_access_denied(self, httpx_mock, key, monkeypatch):
        """httpx times each phase separately, so a slow server is cut off by the overall deadline instead."""
        monkeypatch.setattr(commcarehq_api, "APP_ACCESS_TIMEOUT", 0.1)

        async def stall(request):
            await asyncio.sleep(5)

        self._mock(httpx_mock, key, "app1", json={})
        httpx_mock.add_callback(stall, url=APP_URL.format(url=key.hq_server.url, app_id="app2"))
        ok, error = app_access_errors(key, [("dom", "app1"), ("dom", "app2")])
        assert ok is None  # the check that finished keeps its result
        assert error is not None
        assert not isinstance(error, CommCareHQAccessDenied)


@pytest.mark.django_db
class TestListCalls:
    """The domain and app lists feed the opportunity setup selects, so every failure must raise
    CommCareHQAPIException for the view to turn into a message."""

    @pytest.fixture
    def key(self):
        return HQApiKeyFactory()

    @pytest.fixture(params=["domains", "applications"])
    def fetch(self, request, key):
        if request.param == "domains":
            return lambda: get_domains_for_user(key)
        return lambda: get_applications_for_user_by_domain(key, "dom")

    @pytest.mark.parametrize(
        "status, access_denied", [(401, True), (403, True), (404, True), (500, False), (504, False)]
    )
    def test_only_refusals_are_access_denied(self, httpx_mock, fetch, status, access_denied):
        httpx_mock.add_response(status_code=status)
        with pytest.raises(CommCareHQAPIException) as exc_info:
            fetch()
        assert isinstance(exc_info.value, CommCareHQAccessDenied) is access_denied

    def test_transport_error_raises_hq_exception(self, httpx_mock, fetch):
        httpx_mock.add_exception(httpx.ReadTimeout("timed out"))
        _assert_hq_failure(fetch)

    def test_call_that_outlasts_the_deadline_raises_hq_exception(self, httpx_mock, fetch, monkeypatch):
        monkeypatch.setattr(commcarehq_api, "HQ_LIST_TIMEOUT", 0.1)

        async def stall(request):
            await asyncio.sleep(5)

        httpx_mock.add_callback(stall)
        with pytest.raises(CommCareHQAPIException, match="no answer within"):
            fetch()

    @pytest.mark.parametrize(
        "response",
        [
            {"text": "<html>Down for maintenance</html>"},
            {"json": {"meta": {}}},
            {"json": {"objects": "nope"}},
            {"json": ["not", "a", "dict"]},
            {"json": {"objects": ["not a dict"]}},
        ],
    )
    def test_answer_that_is_not_hqs_list_raises_hq_exception(self, httpx_mock, fetch, response):
        """A proxy or maintenance page can answer 200 with something else entirely."""
        httpx_mock.add_response(**response)
        _assert_hq_failure(fetch)

    def test_error_body_is_truncated(self, httpx_mock, fetch):
        httpx_mock.add_response(status_code=500, text="x" * 10_000)
        with pytest.raises(CommCareHQAPIException) as exc_info:
            fetch()
        assert len(str(exc_info.value)) < 1_000

    def test_lists_domains_and_apps(self, httpx_mock, key):
        httpx_mock.add_response(json={"objects": [{"domain_name": "dom"}]})
        httpx_mock.add_response(
            json={"objects": [{"id": "a1", "name": "App", "versions": [{"is_released": True}]}, {"id": "a2"}]}
        )
        assert get_domains_for_user(key) == ["dom"]
        assert get_applications_for_user_by_domain(key, "dom") == [
            {"id": "a1", "name": "App"},
            {"id": "a2", "name": "Unreleased - None"},
        ]


def _assert_hq_failure(fetch):
    """`fetch` raises CommCareHQAPIException for HQ failing, not CommCareHQAccessDenied for a refused key."""
    with pytest.raises(CommCareHQAPIException) as exc_info:
        fetch()
    assert not isinstance(exc_info.value, CommCareHQAccessDenied)
