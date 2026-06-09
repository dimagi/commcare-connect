import pytest
from django.test import RequestFactory

from commcare_connect.users.helpers import (
    build_hq_user_payload,
    create_hq_user_and_link,
    fetch_hq_user_uuid,
    get_organization_for_request,
)
from commcare_connect.users.models import ConnectIDUserLink
from commcare_connect.users.tests.factories import ConnectIdUserLinkFactory
from commcare_connect.utils.commcarehq_api import CommCareHQAPIException


@pytest.mark.django_db
class TestBuildHqUserPayload:
    def test_includes_all_fields(self, mobile_user):
        mobile_user.name = "Test User"
        mobile_user.phone_number = "+1234567890"

        payload = build_hq_user_payload(mobile_user)

        assert payload == {
            "username": mobile_user.username,
            "connect_username": mobile_user.username,
            "first_name": "Test User",
            "default_phone_number": "+1234567890",
        }

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
        hq_user_uuid = "abc123def456"
        httpx_mock.add_response(
            url=self._get_api_url(opportunity, domain),
            method="POST",
            status_code=201,
            json={"id": hq_user_uuid},
        )

        assert create_hq_user_and_link(mobile_user, domain, opportunity)
        link = ConnectIDUserLink.objects.get(user=mobile_user, domain=domain)
        assert link.commcare_username == f"{mobile_user.username.lower()}@{domain}.commcarehq.org"
        assert link.hq_server == opportunity.hq_server
        assert link.hq_user_uuid == hq_user_uuid

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
        link = ConnectIDUserLink.objects.get(user=mobile_user, domain=domain)
        assert link.hq_user_uuid is None

    def test_raises_on_other_errors(self, mobile_user, opportunity, httpx_mock):
        domain = "test-domain"
        httpx_mock.add_response(
            url=self._get_api_url(opportunity, domain), method="POST", status_code=500, text="server error"
        )

        with pytest.raises(CommCareHQAPIException):
            create_hq_user_and_link(mobile_user, domain, opportunity)


@pytest.mark.django_db
class TestFetchHqUserUuid:
    def _list_url(self, opportunity, domain, query="?limit=200"):
        return f"{opportunity.api_key.hq_server.url}/a/{domain}/api/v0.5/user/{query}"

    def _make_link(self, mobile_user, opportunity, domain, username="alice"):
        return ConnectIdUserLinkFactory(
            user=mobile_user,
            commcare_username=f"{username}@{domain}.commcarehq.org",
            domain=domain,
            hq_server=opportunity.hq_server,
        )

    def test_returns_uuid_when_username_matches(self, mobile_user, opportunity, httpx_mock):
        domain = "test-domain"
        hq_user_uuid = "uuid-alice"
        link = self._make_link(mobile_user, opportunity, domain)
        httpx_mock.add_response(
            url=self._list_url(opportunity, domain),
            method="GET",
            json={
                "meta": {"next": None},
                "objects": [
                    {"id": "uuid-bob", "username": f"bob@{domain}.commcarehq.org"},
                    {"id": hq_user_uuid, "username": f"alice@{domain}.commcarehq.org"},
                ],
            },
        )

        assert fetch_hq_user_uuid(link, opportunity.api_key) == hq_user_uuid

    def test_returns_none_when_no_match(self, mobile_user, opportunity, httpx_mock):
        domain = "test-domain"
        link = self._make_link(mobile_user, opportunity, domain)
        httpx_mock.add_response(
            url=self._list_url(opportunity, domain),
            method="GET",
            json={
                "meta": {"next": None},
                "objects": [{"id": "uuid-bob", "username": f"bob@{domain}.commcarehq.org"}],
            },
        )

        assert fetch_hq_user_uuid(link, opportunity.api_key) is None

    def test_follows_pagination_until_match(self, mobile_user, opportunity, httpx_mock):
        domain = "test-domain"
        hq_user_uuid = "uuid-alice"
        link = self._make_link(mobile_user, opportunity, domain)
        next_path = f"/a/{domain}/api/v0.5/user/?limit=200&offset=200"
        httpx_mock.add_response(
            url=self._list_url(opportunity, domain),
            method="GET",
            json={
                "meta": {"next": next_path},
                "objects": [{"id": "uuid-bob", "username": f"bob@{domain}.commcarehq.org"}],
            },
        )
        httpx_mock.add_response(
            url=f"{opportunity.api_key.hq_server.url}{next_path}",
            method="GET",
            json={
                "meta": {"next": None},
                "objects": [{"id": hq_user_uuid, "username": f"alice@{domain}.commcarehq.org"}],
            },
        )

        assert fetch_hq_user_uuid(link, opportunity.api_key) == hq_user_uuid

    def test_raises_on_http_error(self, mobile_user, opportunity, httpx_mock):
        domain = "test-domain"
        link = self._make_link(mobile_user, opportunity, domain)
        httpx_mock.add_response(
            url=self._list_url(opportunity, domain), method="GET", status_code=500, text="server error"
        )

        with pytest.raises(CommCareHQAPIException):
            fetch_hq_user_uuid(link, opportunity.api_key)


@pytest.mark.django_db
class TestGetOrganizationForRequest:
    def test_returns_org_by_slug(self, rf: RequestFactory, user, organization):
        request = rf.get("/fake-url/")
        request.user = user
        result = get_organization_for_request(request, {"org_slug": organization.slug})
        assert result == organization

    def test_returns_none_for_invalid_slug(self, rf: RequestFactory, user):
        request = rf.get("/fake-url/")
        request.user = user
        assert get_organization_for_request(request, {"org_slug": "nonexistent"}) is None

    def test_returns_first_membership_org_when_no_slug(self, rf: RequestFactory, org_user_member, organization):
        request = rf.get("/fake-url/")
        request.user = org_user_member
        result = get_organization_for_request(request, {})
        assert result == organization

    def test_returns_none_when_no_slug_and_no_membership(self, rf: RequestFactory, user):
        request = rf.get("/fake-url/")
        request.user = user
        assert get_organization_for_request(request, {}) is None
