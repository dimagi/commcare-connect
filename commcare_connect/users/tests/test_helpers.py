import pytest

from commcare_connect.users.helpers import build_hq_user_payload, create_hq_user_and_link
from commcare_connect.users.models import ConnectIDUserLink
from commcare_connect.utils.commcarehq_api import CommCareHQAPIException


@pytest.mark.django_db
class TestBuildHqUserPayload:
    def test_includes_all_fields(self, mobile_user):
        mobile_user.name = "Test User"
        mobile_user.email = "test@example.com"
        mobile_user.phone_number = "+1234567890"

        payload = build_hq_user_payload(mobile_user)

        assert payload == {
            "username": mobile_user.username,
            "connect_username": mobile_user.username,
            "first_name": "Test User",
            "email": "test@example.com",
            "default_phone_number": "+1234567890",
        }

    def test_excludes_email_when_null(self, mobile_user):
        mobile_user.email = None

        payload = build_hq_user_payload(mobile_user)

        assert "email" not in payload

    def test_excludes_name_when_blank(self, mobile_user):
        mobile_user.name = ""

        payload = build_hq_user_payload(mobile_user)

        assert "first_name" not in payload

    def test_excludes_phone_when_null(self, mobile_user):
        mobile_user.phone_number = None

        payload = build_hq_user_payload(mobile_user)

        assert "default_phone_number" not in payload


@pytest.mark.django_db
class TestCreateHqUserAndLink:
    def _get_api_url(self, opportunity, domain):
        return f"{opportunity.api_key.hq_server.url}/a/{domain}/api/v0.5/user/"

    def test_creates_user_and_link(self, mobile_user, opportunity, httpx_mock):
        domain = "test-domain"
        httpx_mock.add_response(url=self._get_api_url(opportunity, domain), method="POST", status_code=201)

        assert create_hq_user_and_link(mobile_user, domain, opportunity)
        link = ConnectIDUserLink.objects.get(user=mobile_user, domain=domain)
        assert link.commcare_username == f"{mobile_user.username.lower()}@{domain}.commcarehq.org"
        assert link.hq_server == opportunity.hq_server

    def test_skips_creation_when_link_exists(self, mobile_user, opportunity):
        domain = "test-domain"
        ConnectIDUserLink.objects.create(
            user=mobile_user, domain=domain, hq_server=opportunity.hq_server, commcare_username="existing"
        )

        assert create_hq_user_and_link(mobile_user, domain, opportunity)
        assert ConnectIDUserLink.objects.filter(user=mobile_user, domain=domain).count() == 1

    def test_creates_link_when_username_already_taken(self, mobile_user, opportunity, httpx_mock):
        domain = "test-domain"
        httpx_mock.add_response(
            url=self._get_api_url(opportunity, domain), method="POST", status_code=400, text="username already taken"
        )

        assert create_hq_user_and_link(mobile_user, domain, opportunity)
        assert ConnectIDUserLink.objects.filter(user=mobile_user, domain=domain).exists()

    def test_raises_on_other_errors(self, mobile_user, opportunity, httpx_mock):
        domain = "test-domain"
        httpx_mock.add_response(
            url=self._get_api_url(opportunity, domain), method="POST", status_code=500, text="server error"
        )

        with pytest.raises(CommCareHQAPIException):
            create_hq_user_and_link(mobile_user, domain, opportunity)
