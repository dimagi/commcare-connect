import httpx
import pytest

from commcare_connect.opportunity.tests.factories import HQApiKeyFactory
from commcare_connect.utils.commcarehq_api import CommCareHQAccessDenied, app_access_errors

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
