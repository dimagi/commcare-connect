import pytest
from django.contrib.auth.models import AnonymousUser

# Removed unused import
from django.test import RequestFactory
from django.urls import reverse

from commcare_connect.solicitations.views import SolicitationAccessMixin, SolicitationManagerMixin
from commcare_connect.users.tests.factories import UserFactory


class DummyAccessView(SolicitationAccessMixin):
    """Dummy view for testing SolicitationAccessMixin"""

    def get(self, request):
        return "success"


class DummyManagerView(SolicitationManagerMixin):
    """Dummy view for testing SolicitationManagerMixin"""

    def get(self, request):
        return "success"


@pytest.mark.django_db
class TestSolicitationAccessMixin:
    """Test access control for organization membership requirement"""

    def setup_method(self):
        self.factory = RequestFactory()
        self.view = DummyAccessView()

    def test_user_with_organization_has_access(self, user, organization):
        """Test that users with organization membership can access"""
        membership = user.memberships.create(organization=organization)
        request = self.factory.get("/test/")
        request.user = user
        request.org_membership = membership

        self.view.request = request

        assert self.view.test_func() is True

    def test_user_without_organization_denied_access(self):
        """Test that users without organization membership are denied"""
        user = UserFactory()
        request = self.factory.get("/test/")
        request.user = user
        request.org_membership = None

        self.view.request = request

        assert self.view.test_func() is False

    def test_superuser_has_access_without_organization(self):
        """Test that superusers can access without organization membership"""
        superuser = UserFactory(is_superuser=True)
        request = self.factory.get("/test/")
        request.user = superuser
        request.org_membership = None

        self.view.request = request

        assert self.view.test_func() is True

    def test_anonymous_user_denied_access(self):
        """Test that anonymous users are denied access"""
        request = self.factory.get("/test/")
        request.user = AnonymousUser()
        request.org_membership = None

        self.view.request = request

        assert self.view.test_func() is False


@pytest.mark.django_db
class TestSolicitationManagerMixin:
    """Test access control for solicitation management"""

    def setup_method(self):
        self.factory = RequestFactory()
        self.view = DummyManagerView()

    def test_user_with_organization_has_manager_access(self, user, organization):
        """Test that users with admin role and program manager org have manager access"""
        request = self.factory.get("/test/")
        request.user = user

        # Set up organization with program manager capability
        organization.program_manager = True
        organization.save()

        # Set up membership as admin
        membership = user.memberships.create(organization=organization)
        membership.role = membership.Role.ADMIN
        membership.save()

        request.org = organization
        request.org_membership = membership

        self.view.request = request

        assert self.view.test_func() is True

    def test_user_without_admin_role_denied_manager_access(self, user, organization):
        """Test that non-admin users are denied manager access"""
        request = self.factory.get("/test/")
        request.user = user

        # Set up organization with program manager capability
        organization.program_manager = True
        organization.save()

        # Keep membership as non-admin (default)
        membership = user.memberships.create(organization=organization)

        request.org = organization
        request.org_membership = membership

        self.view.request = request

        assert self.view.test_func() is False

    def test_superuser_has_manager_access(self):
        """Test that superusers have manager access"""
        superuser = UserFactory(is_superuser=True)
        request = self.factory.get("/test/")
        request.user = superuser
        request.org_membership = None

        self.view.request = request

        assert self.view.test_func() is True


@pytest.mark.django_db
class TestViewAccessIntegration:
    """Integration tests for actual view access"""

    def test_dashboard_requires_authentication(self, client):
        """Test that dashboard redirects unauthenticated users"""
        url = reverse("solicitations:dashboard")
        response = client.get(url)

        # Should redirect to login
        assert response.status_code == 302
        assert "login" in response.url

    def test_dashboard_accessible_to_user_with_org(self, client, user, organization):
        """Test that dashboard is accessible to users with organization"""
        user.memberships.create(organization=organization)
        client.force_login(user)
        url = reverse("solicitations:dashboard")

        response = client.get(url)

        assert response.status_code == 200

    def test_dashboard_forbidden_to_user_without_org(self, client):
        """Test that dashboard returns 403 for users without organization"""
        user = UserFactory()
        client.force_login(user)
        url = reverse("solicitations:dashboard")

        response = client.get(url)

        assert response.status_code == 403
