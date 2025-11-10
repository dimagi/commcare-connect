import pytest
from django.http import HttpResponse
from django.urls import clear_url_caches, path, reverse
from django.views import View

from commcare_connect.opportunity.views import OrganizationUserMemberRoleMixin, OrganizationUserMixin
from commcare_connect.organization.decorators import org_admin_required, org_member_required, org_viewer_required
from commcare_connect.organization.urls import urlpatterns as org_url_patterns
from commcare_connect.users.tests.factories import UserFactory
from commcare_connect.utils.tests import check_basic_permissions


class TestAllOrgAccessPermission:
    @pytest.fixture(autouse=True)
    def setup(self, db):
        clear_url_caches()

        @org_member_required
        def dummy_member_view(request, org_slug):
            return HttpResponse("OK")

        @org_admin_required
        def dummy_admin_view(request, org_slug):
            return HttpResponse("OK")

        @org_viewer_required
        def dummy_viewer_view(request, org_slug):
            return HttpResponse("OK")

        # Dummy class-based vie
        class DummyOrgViewerView(OrganizationUserMemberRoleMixin, View):
            def get(self, request, *args, **kwargs):
                return HttpResponse("OK")

        class DummyOrgMemberView(OrganizationUserMixin, View):
            def get(self, request, *args, **kwargs):
                return HttpResponse("OK")

        # Add dummy views to URLs
        org_url_patterns.extend(
            [
                path("admin_fbv/", dummy_admin_view, name="admin_fbv"),
                path("viewer_fbv/", dummy_viewer_view, name="viewer_fbv"),
                path("member_fbv/", dummy_member_view, name="member_fbv"),
                path("member_cbv/", DummyOrgViewerView.as_view(), name="member_cbv"),
                path("viewer_cbv/", DummyOrgMemberView.as_view(), name="viewer_cbv"),
            ]
        )

    @pytest.mark.parametrize("url_name", ["admin_fbv", "viewer_fbv", "member_fbv", "member_cbv", "viewer_cbv"])
    def test_permissions(self, url_name, organization):
        url = reverse(f"organization:{url_name}", args=(organization.slug,))
        check_basic_permissions(UserFactory(), url, "all_org_access", 404)
