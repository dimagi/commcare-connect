import httpx
import pytest

from commcare_connect.opportunity.tests.factories import HQApiKeyFactory
from commcare_connect.utils.commcarehq_api import (
    CommCareHQAccessDenied,
    CommCareHQAPIException,
    CommCareHQUnreachable,
    verify_app_access,
)

APP_URL = "{url}/a/dom/api/v0.5/application/app1/"


@pytest.mark.django_db
class TestVerifyAppAccess:
    def test_passes_on_200(self, httpx_mock):
        key = HQApiKeyFactory()
        httpx_mock.add_response(url=APP_URL.format(url=key.hq_server.url), json={"id": "app1"})
        verify_app_access(key, "dom", "app1")  # no exception

    @pytest.mark.parametrize(
        "status, access_denied",
        [(401, True), (403, True), (404, True), (429, False), (500, False), (502, False), (503, False)],
    )
    def test_only_refusals_are_access_denied(self, httpx_mock, status, access_denied):
        key = HQApiKeyFactory()
        httpx_mock.add_response(url=APP_URL.format(url=key.hq_server.url), status_code=status)
        with pytest.raises(CommCareHQAPIException) as exc:
            verify_app_access(key, "dom", "app1")
        assert isinstance(exc.value, CommCareHQAccessDenied) is access_denied
        assert not isinstance(exc.value, CommCareHQUnreachable)

    def test_raises_unreachable_on_transport_error(self, httpx_mock):
        key = HQApiKeyFactory()
        httpx_mock.add_exception(httpx.ConnectTimeout("timed out"))
        with pytest.raises(CommCareHQUnreachable):
            verify_app_access(key, "dom", "app1")
