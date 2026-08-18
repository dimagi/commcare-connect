import pytest
from django.http import HttpResponse
from django.urls import clear_url_caches, path, reverse
from django.views import View

from commcare_connect.organization.decorators import (
    OrgManageAccessMixin,
    OrgViewAccessMixin,
    org_manage_access_required,
    org_standard_access_required,
    org_view_access_required,
)
from commcare_connect.organization.urls import urlpatterns as org_url_patterns
from commcare_connect.users.tests.factories import UserFactory
from commcare_connect.utils.test_utils import check_basic_permissions


class TestAllOrgAccessPermission:
    @pytest.fixture(autouse=True)
    def setup(self, db):
        clear_url_caches()

        # These mount on org-level URLs, which carry no opportunity, so they take the org gates.
        @org_standard_access_required
        def dummy_member_view(request, org_slug):
            return HttpResponse("OK")

        @org_manage_access_required
        def dummy_admin_view(request, org_slug):
            return HttpResponse("OK")

        @org_view_access_required
        def dummy_viewer_view(request, org_slug):
            return HttpResponse("OK")

        class DummyAdminMixinView(OrgManageAccessMixin, View):
            def get(self, request, *args, **kwargs):
                return HttpResponse("OK")

        class DummyViewerMixinView(OrgViewAccessMixin, View):
            def get(self, request, *args, **kwargs):
                return HttpResponse("OK")

        # Add dummy views to URLs
        org_url_patterns.extend(
            [
                path("admin_fbv/", dummy_admin_view, name="admin_fbv"),
                path("viewer_fbv/", dummy_viewer_view, name="viewer_fbv"),
                path("member_fbv/", dummy_member_view, name="member_fbv"),
                path("admin_cbv/", DummyAdminMixinView.as_view(), name="admin_cbv"),
                path("viewer_cbv/", DummyViewerMixinView.as_view(), name="viewer_cbv"),
            ]
        )

    @pytest.mark.parametrize("url_name", ["admin_fbv", "viewer_fbv", "member_fbv", "admin_cbv", "viewer_cbv"])
    def test_permissions(self, url_name, organization):
        url = reverse(f"organization:{url_name}", args=(organization.slug,))
        check_basic_permissions(UserFactory(), url, "all_org_access", 404)
