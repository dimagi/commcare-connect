from datetime import timedelta
from urllib.parse import unquote

import pytest
from django.contrib.auth.models import Permission
from django.contrib.messages import get_messages
from django.urls import reverse
from django.utils import timezone

from commcare_connect.organization.models import (
    Organization,
    OrganizationInvite,
    UserOrganizationMembership,
)
from commcare_connect.users.models import User
from commcare_connect.users.tests.factories import OrganizationInviteFactory, UserFactory


@pytest.mark.django_db
class TestRemoveMembersView:
    def url(self, org_slug):
        return reverse("organization:remove_members", args=(org_slug,))

    def test_non_admin_cannot_access(self, client, org_user_member, organization):
        client.force_login(org_user_member)
        response = client.post(
            self.url(org_slug=organization.slug),
            data={},
        )
        assert response.status_code == 404

    def test_admin_cannot_remove_self(self, client, org_user_admin, organization):
        membership = UserOrganizationMembership.objects.get(user=org_user_admin, organization=organization)

        client.force_login(org_user_admin)
        response = client.post(
            self.url(org_slug=organization.slug),
            data={"membership_ids": [membership.id]},
        )

        assert response.status_code == 302
        messages = list(get_messages(response.wsgi_request))
        assert len(messages) == 1
        assert str(messages[0]) == "You cannot remove yourself from the workspace."

        assert UserOrganizationMembership.objects.filter(id=membership.id).exists()

    def test_admin_can_remove_others(self, client, org_user_admin, org_user_member, organization):
        other_membership = UserOrganizationMembership.objects.get(user=org_user_member, organization=organization)

        client.force_login(org_user_admin)
        response = client.post(
            self.url(org_slug=organization.slug),
            data={"membership_ids": [other_membership.id]},
        )

        assert response.status_code == 302
        messages = list(get_messages(response.wsgi_request))
        assert len(messages) == 1
        assert str(messages[0]) == "Selected members have been removed from the workspace."

        assert not UserOrganizationMembership.objects.filter(id=other_membership.id).exists()

    def test_request_fails_when_admin_in_list(self, client, org_user_admin, org_user_member, organization):
        admin_memebership = UserOrganizationMembership.objects.get(user=org_user_admin, organization=organization)
        other_membership = UserOrganizationMembership.objects.get(user=org_user_member, organization=organization)

        client.force_login(org_user_admin)
        response = client.post(
            self.url(org_slug=organization.slug),
            data={"membership_ids": [admin_memebership.id, other_membership.id]},
        )

        assert response.status_code == 302
        messages = list(get_messages(response.wsgi_request))
        assert len(messages) == 1
        assert str(messages[0]) == "You cannot remove yourself from the workspace."

        assert UserOrganizationMembership.objects.filter(id=other_membership.id).exists()


@pytest.mark.django_db
class TestOrganizationHomeView:
    def url(self, org_slug):
        return reverse("organization:home", args=(org_slug,))

    def test_program_manager_requires_permission(self, client, org_user_admin, organization):
        organization.program_manager = False
        organization.save(update_fields=["program_manager"])

        client.force_login(org_user_admin)
        response = client.post(
            self.url(org_slug=organization.slug),
            data={"name": organization.name, "program_manager": "on"},
        )

        assert response.status_code == 302
        organization.refresh_from_db()
        assert not organization.program_manager

    def test_program_manager_updates_with_permission(self, client, org_user_admin, organization):
        organization.program_manager = False
        organization.save(update_fields=["program_manager"])
        permission = Permission.objects.get(codename="org_management_settings_access")
        org_user_admin.user_permissions.add(permission)
        org_user_admin.refresh_from_db()

        client.force_login(org_user_admin)
        response = client.post(
            self.url(org_slug=organization.slug),
            data={"name": organization.name, "program_manager": "on"},
        )

        assert response.status_code == 302
        organization.refresh_from_db()
        assert organization.program_manager


@pytest.mark.django_db
class TestOrganizationCreateView:
    def url(self):
        return reverse("organization_create")

    def test_new_org_creates_admin_membership(self, client, user):
        org_name = f"New Workspace {user.pk}"
        client.force_login(user)
        response = client.post(self.url(), data={"name": org_name})

        assert response.status_code == 302
        org = Organization.objects.get(name=org_name)
        assert response.url == reverse("opportunity:list", args=(org.slug,))
        membership = UserOrganizationMembership.objects.get(user=user, organization=org)
        assert membership.role == UserOrganizationMembership.Role.ADMIN
        assert org.verified is False

    def test_profile_fields_are_saved(self, client, user):
        client.force_login(user)

        org_name = f"Profiled Workspace {user.pk}"
        response = client.post(
            self.url(),
            data={
                "name": org_name,
                "short_name": "PW",
                "year_of_establishment": 2005,
                "website": "https://example.com",
                "contact_emails": "one@example.com",
            },
        )

        assert response.status_code == 302
        org = Organization.objects.get(name=org_name)
        assert org.short_name == "PW"
        assert org.year_of_establishment == 2005
        assert org.contact_emails == "one@example.com"

    def test_duplicate_name_does_not_create_org(self, client, user, organization):
        client.force_login(user)

        response = client.post(self.url(), data={"name": organization.name})

        assert response.status_code == 200
        assert Organization.objects.filter(name=organization.name).count() == 1
        assert not UserOrganizationMembership.objects.filter(user=user, organization=organization).exists()


@pytest.mark.django_db
class TestNoOrganizationView:
    def url(self):
        return reverse("no_organization")

    def test_membership_less_user_is_offered_org_creation(self, client, user):
        client.force_login(user)
        response = client.get(self.url())

        assert response.status_code == 200
        assert reverse("organization_create") in response.content.decode()

    def test_member_is_redirected_to_their_workspace(self, client, org_user_member):
        client.force_login(org_user_member)
        response = client.get(self.url())

        assert response.status_code == 302
        assert response.url == reverse("users:redirect")

    def test_anonymous_user_is_redirected_to_login(self, client):
        response = client.get(self.url())

        assert response.status_code == 302
        assert reverse("account_login") in response.url


@pytest.mark.django_db
class TestAcceptInviteView:
    @staticmethod
    def _url(org_slug, token):
        return reverse("organization:accept_invite", args=(org_slug, token))

    def test_invalid_token_returns_404(self, client, organization):
        response = client.get(self._url(organization.slug, "nonexistent-token"))
        assert response.status_code == 404

    @pytest.mark.parametrize("status", [None, OrganizationInvite.Status.REVOKED])
    def test_expired_or_revoked_invite_is_rejected(self, client, organization, status):
        kwargs = {"status": status} if status else {}
        invite = OrganizationInviteFactory(organization=organization, **kwargs)
        if status is None:
            OrganizationInvite.objects.filter(pk=invite.pk).update(
                date_modified=timezone.now() - timedelta(days=OrganizationInvite.EXPIRY_DAYS + 1)
            )

        response = client.get(self._url(organization.slug, invite.token))

        assert response.status_code == 302
        assert response.url == reverse("account_login")
        invite.refresh_from_db()
        # An expired invite is never persisted as such — it's only reset when re-invited via send_invite().
        assert invite.status == (OrganizationInvite.Status.INVITED if status is None else status)

    def test_authenticated_matching_email_accepts_and_creates_membership(self, client, organization):
        user = UserFactory(email="invitee@example.com")
        invite = OrganizationInviteFactory(organization=organization, email=user.email, role="member")
        client.force_login(user)

        response = client.get(self._url(organization.slug, invite.token))

        assert response.status_code == 302
        assert response.url == reverse("opportunity:list", args=(organization.slug,))
        assert UserOrganizationMembership.objects.filter(user=user, organization=organization, role="member").exists()
        invite.refresh_from_db()
        assert invite.status == OrganizationInvite.Status.ACCEPTED

    def test_authenticated_mismatched_email_does_not_accept(self, client, organization):
        user = UserFactory(email="someone-else@example.com")
        invite = OrganizationInviteFactory(organization=organization, email="invitee@example.com")
        client.force_login(user)

        response = client.get(self._url(organization.slug, invite.token))

        assert response.status_code == 302
        assert not UserOrganizationMembership.objects.filter(user=user, organization=organization).exists()
        invite.refresh_from_db()
        assert invite.status == OrganizationInvite.Status.INVITED

    def test_unauthenticated_existing_account_redirects_to_login(self, client, organization):
        existing_user = UserFactory(email="invitee@example.com")
        invite = OrganizationInviteFactory(organization=organization, email=existing_user.email)

        response = client.get(self._url(organization.slug, invite.token))

        assert response.status_code == 302
        assert response.url.startswith(reverse("account_login"))
        assert self._url(organization.slug, invite.token) in unquote(response.url)

    def test_unauthenticated_new_user_renders_join_form(self, client, organization):
        invite = OrganizationInviteFactory(organization=organization, email="brand-new@example.com")

        response = client.get(self._url(organization.slug, invite.token))

        assert response.status_code == 200
        assert "form" in response.context

    def test_unauthenticated_new_user_can_join(self, client, organization):
        invite = OrganizationInviteFactory(organization=organization, email="brand-new@example.com", role="admin")

        response = client.post(
            self._url(organization.slug, invite.token),
            data={"password1": "a-very-strong-password-1", "password2": "a-very-strong-password-1", "agree": "on"},
        )

        assert response.status_code == 302
        assert response.url == reverse("opportunity:list", args=(organization.slug,))
        new_user = User.objects.get(email="brand-new@example.com")
        assert UserOrganizationMembership.objects.filter(
            user=new_user, organization=organization, role="admin"
        ).exists()
        invite.refresh_from_db()
        assert invite.status == OrganizationInvite.Status.ACCEPTED

    def test_unauthenticated_new_user_join_requires_matching_passwords(self, client, organization):
        invite = OrganizationInviteFactory(organization=organization, email="brand-new@example.com")

        response = client.post(
            self._url(organization.slug, invite.token),
            data={"password1": "a-very-strong-password-1", "password2": "different-password", "agree": "on"},
        )

        assert response.status_code == 200
        assert not User.objects.filter(email="brand-new@example.com").exists()


@pytest.mark.django_db
class TestRevokeInviteView:
    @staticmethod
    def _url(org_slug, invite_id):
        return reverse("organization:revoke_invite", args=(org_slug, invite_id))

    def test_admin_can_revoke_pending_invite(self, client, org_user_admin, organization):
        invite = OrganizationInviteFactory(organization=organization)
        client.force_login(org_user_admin)

        response = client.post(self._url(organization.slug, invite.pk))

        assert response.status_code == 200
        invite.refresh_from_db()
        assert invite.status == OrganizationInvite.Status.REVOKED

    def test_member_cannot_revoke_invite(self, client, org_user_member, organization):
        invite = OrganizationInviteFactory(organization=organization)
        client.force_login(org_user_member)

        response = client.post(self._url(organization.slug, invite.pk))

        assert response.status_code == 404
        invite.refresh_from_db()
        assert invite.status == OrganizationInvite.Status.INVITED


@pytest.mark.django_db
class TestOrgMemberTableView:
    @staticmethod
    def _url(org_slug):
        return reverse("organization:org_member_table", args=(org_slug,))

    def test_admin_can_access(self, client, org_user_admin, organization):
        client.force_login(org_user_admin)
        response = client.get(self._url(organization.slug))
        assert response.status_code == 200

    def test_member_cannot_access(self, client, org_user_member, organization):
        client.force_login(org_user_member)
        response = client.get(self._url(organization.slug))
        assert response.status_code == 404

    def test_unauthenticated_user_is_redirected(self, client, organization):
        response = client.get(self._url(organization.slug))
        assert response.status_code == 302
        assert "login" in response.url


@pytest.mark.django_db
class TestPendingInvitesTableView:
    @staticmethod
    def _url(org_slug):
        return reverse("organization:pending_invites_table", args=(org_slug,))

    def test_lists_only_pending_invites(self, client, org_user_admin, organization):
        pending = OrganizationInviteFactory(organization=organization, email="pending@example.com")
        OrganizationInviteFactory(organization=organization, status=OrganizationInvite.Status.ACCEPTED)
        expired = OrganizationInviteFactory(organization=organization, email="expired@example.com")
        OrganizationInvite.objects.filter(pk=expired.pk).update(
            date_modified=timezone.now() - timedelta(days=OrganizationInvite.EXPIRY_DAYS + 1)
        )
        client.force_login(org_user_admin)

        response = client.get(self._url(organization.slug))

        assert response.status_code == 200
        assert pending.email.encode() in response.content
        assert expired.email.encode() not in response.content

    def test_member_cannot_access(self, client, org_user_member, organization):
        client.force_login(org_user_member)
        response = client.get(self._url(organization.slug))
        assert response.status_code == 404
