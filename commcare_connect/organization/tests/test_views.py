import re
from datetime import timedelta
from unittest.mock import patch
from urllib.parse import unquote

import pytest
from django.contrib.auth.models import Permission
from django.contrib.messages import get_messages
from django.urls import reverse
from django.utils import timezone
from django.utils.timezone import localtime

from commcare_connect.organization.models import (
    LLOEntity,
    Organization,
    OrganizationInvite,
    UserOrganizationMembership,
)
from commcare_connect.users.models import User
from commcare_connect.users.tests.factories import OrganizationInviteFactory, UserFactory
from commcare_connect.utils.forms import TOMSELECT_NEW_ENTRY_PREFIX
from commcare_connect.utils.tables import DATE_TIME_FORMAT


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

    def test_existing_org_does_not_create_membership(self, client, org_user_member, organization):
        existing_llo = LLOEntity.objects.create(name="Existing LLO")
        organization.llo_entity = existing_llo
        organization.save(update_fields=["llo_entity"])

        client.force_login(org_user_member)
        response = client.post(
            self.url(),
            data={
                "org": str(organization.pk),
                "llo_entity": str(existing_llo.pk),
            },
        )

        assert response.status_code == 302
        assert response.url == reverse("opportunity:list", args=(organization.slug,))
        assert UserOrganizationMembership.objects.filter(user=org_user_member, organization=organization).count() == 1

    def test_new_org_creates_admin_membership(self, client, user):
        # A user with no memberships sees no existing LLO Entities, so they create one.
        org_name = f"New Workspace {user.pk}"
        client.force_login(user)
        response = client.post(
            self.url(),
            data={
                "org": TOMSELECT_NEW_ENTRY_PREFIX + org_name,
                "llo_entity": TOMSELECT_NEW_ENTRY_PREFIX + f"New Org LLO {user.pk}",
                "llo_entity_short_name": "NOL",
            },
        )

        assert response.status_code == 302
        org = Organization.objects.get(name=org_name)
        assert response.url == reverse("opportunity:list", args=(org.slug,))
        membership = UserOrganizationMembership.objects.get(user=user, organization=org)
        assert membership.role == UserOrganizationMembership.Role.ADMIN
        assert org.verified is False


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
class TestReinviteView:
    @staticmethod
    def _url(org_slug, invite_id):
        return reverse("organization:reinvite", args=(org_slug, invite_id))

    @staticmethod
    def _cooldown_of(minutes):
        """Pinned so these tests cover the throttle logic, not whatever REINVITE_COOLDOWN is tuned to."""
        return patch.object(OrganizationInvite, "REINVITE_COOLDOWN", timedelta(minutes=minutes))

    @staticmethod
    def _past_cooldown(invite):
        OrganizationInvite.objects.filter(pk=invite.pk).update(
            date_modified=timezone.now() - OrganizationInvite.REINVITE_COOLDOWN - timedelta(minutes=1)
        )
        invite.refresh_from_db()

    def test_admin_reinvite_issues_a_new_token_and_expiry(self, client, org_user_admin, organization):
        invite = OrganizationInviteFactory(organization=organization, role="member")
        self._past_cooldown(invite)
        old_token, old_modified = invite.token, invite.date_modified
        client.force_login(org_user_admin)

        with patch("commcare_connect.organization.views.send_org_invite") as send_mock:
            response = client.post(self._url(organization.slug, invite.pk))

        assert response.status_code == 200
        send_mock.assert_called_once_with(invite_id=invite.pk)
        invite.refresh_from_db()
        assert invite.token != old_token
        assert invite.date_modified > old_modified
        assert invite.role == "member"

    def test_reinvite_is_refused_during_the_cooldown(self, client, org_user_admin, organization):
        invite = OrganizationInviteFactory(organization=organization)
        old_token = invite.token
        client.force_login(org_user_admin)

        with self._cooldown_of(minutes=5), patch("commcare_connect.organization.views.send_org_invite") as send_mock:
            response = client.post(self._url(organization.slug, invite.pk))

        assert response.status_code == 200
        send_mock.assert_not_called()
        invite.refresh_from_db()
        assert invite.token == old_token

    @pytest.mark.parametrize("status", [OrganizationInvite.Status.ACCEPTED, OrganizationInvite.Status.REVOKED])
    def test_only_pending_invites_can_be_reinvited(self, client, org_user_admin, organization, status):
        invite = OrganizationInviteFactory(organization=organization, status=status)
        self._past_cooldown(invite)
        client.force_login(org_user_admin)

        with patch("commcare_connect.organization.views.send_org_invite") as send_mock:
            response = client.post(self._url(organization.slug, invite.pk))

        assert response.status_code == 404
        send_mock.assert_not_called()

    def test_refused_reinvite_reports_the_cooldown_in_a_message(self, client, org_user_admin, organization):
        invite = OrganizationInviteFactory(organization=organization, email="jo@example.com")
        client.force_login(org_user_admin)

        with self._cooldown_of(minutes=5), patch("commcare_connect.organization.views.send_org_invite"):
            content = client.post(self._url(organization.slug, invite.pk)).content.decode()

        assert 'hx-swap-oob="beforeend:#messages"' in content
        assert "An invite was just sent to jo@example.com." in content

    def test_member_cannot_reinvite(self, client, org_user_member, organization):
        invite = OrganizationInviteFactory(organization=organization)
        self._past_cooldown(invite)
        old_token = invite.token
        client.force_login(org_user_member)

        with patch("commcare_connect.organization.views.send_org_invite") as send_mock:
            response = client.post(self._url(organization.slug, invite.pk))

        assert response.status_code == 404
        send_mock.assert_not_called()
        invite.refresh_from_db()
        assert invite.token == old_token


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

    def test_table_shows_the_expiry_date(self, client, org_user_admin, organization):
        invite = OrganizationInviteFactory(organization=organization)
        client.force_login(org_user_admin)

        content = client.get(self._url(organization.slug)).content.decode()

        assert "Expires on" in content
        assert localtime(invite.expiry_date).strftime(DATE_TIME_FORMAT) in content

    @pytest.mark.parametrize("minutes_ago,disabled", [(1, True), (10, False)])
    def test_reinvite_button_is_disabled_during_the_cooldown(
        self, client, org_user_admin, organization, minutes_ago, disabled
    ):
        invite = OrganizationInviteFactory(organization=organization)
        OrganizationInvite.objects.filter(pk=invite.pk).update(
            date_modified=timezone.now() - timedelta(minutes=minutes_ago)
        )
        client.force_login(org_user_admin)

        with patch.object(OrganizationInvite, "REINVITE_COOLDOWN", timedelta(minutes=5)):
            content = client.get(self._url(organization.slug)).content.decode()

        reinvite_url = reverse("organization:reinvite", args=(organization.slug, invite.pk))
        button = re.search(r"<button[^>]*" + re.escape(reinvite_url) + r"[^>]*>", content)
        assert button, "reinvite button was not rendered"
        assert ("disabled" in button.group(0)) is disabled

    def test_member_cannot_access(self, client, org_user_member, organization):
        client.force_login(org_user_member)
        response = client.get(self._url(organization.slug))
        assert response.status_code == 404
