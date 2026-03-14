import pytest
from django.test import RequestFactory

from commcare_connect.users.helpers import get_organization_for_request


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
