from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest
from allauth.account.models import EmailAddress
from django.contrib.auth.models import Permission
from django.contrib.messages import get_messages
from django.urls import reverse
from django.utils import timezone

from commcare_connect.organization.forms import OrganizationInviteForm
from commcare_connect.organization.models import (
    LLOEntity,
    Organization,
    OrganizationInvite,
    UserOrganizationMembership,
)
from commcare_connect.users.models import User
from commcare_connect.users.tests.factories import UserFactory
from commcare_connect.utils.forms import TOMSELECT_NEW_ENTRY_PREFIX


def make_org_invite(organization, email, **kwargs):
    return OrganizationInvite.objects.create(
        organization=organization,
        email=email,
        created_by="host@example.com",
        modified_by="host@example.com",
        **kwargs,
    )


def expire_invite(invite):
    # date_created is auto_now_add, so backdate it via an update to simulate an aged invite
    OrganizationInvite.objects.filter(pk=invite.pk).update(
        date_created=timezone.now() - timedelta(days=OrganizationInvite.EXPIRY_DAYS + 1)
    )


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

    def test_existing_org_does_not_create_membership(self, client, user, organization):
        existing_llo = LLOEntity.objects.create(name="Existing LLO")
        organization.llo_entity = existing_llo
        organization.save(update_fields=["llo_entity"])

        permission = Permission.objects.get(codename="workspace_entity_management_access")
        user.user_permissions.add(permission)

        client.force_login(user)
        response = client.post(
            self.url(),
            data={
                "org": str(organization.pk),
                "llo_entity": str(existing_llo.pk),
            },
        )

        assert response.status_code == 302
        assert response.url == reverse("opportunity:list", args=(organization.slug,))
        assert not UserOrganizationMembership.objects.filter(user=user, organization=organization).exists()

    def test_new_org_creates_admin_membership(self, client, user):
        existing_llo = LLOEntity.objects.create(name="New Org LLO")
        permission = Permission.objects.get(codename="workspace_entity_management_access")
        user.user_permissions.add(permission)

        org_name = f"New Workspace {user.pk}"
        client.force_login(user)
        response = client.post(
            self.url(),
            data={
                "org": TOMSELECT_NEW_ENTRY_PREFIX + org_name,
                "llo_entity": str(existing_llo.pk),
            },
        )

        assert response.status_code == 302
        org = Organization.objects.get(name=org_name)
        assert response.url == reverse("opportunity:list", args=(org.slug,))
        membership = UserOrganizationMembership.objects.get(user=user, organization=org)
        assert membership.role == UserOrganizationMembership.Role.ADMIN


@pytest.mark.django_db
class TestAcceptInviteView:
    @staticmethod
    def _url(org_slug, token):
        return reverse("organization:accept_invite", args=(org_slug, token))

    @pytest.mark.parametrize("role", [UserOrganizationMembership.Role.ADMIN, UserOrganizationMembership.Role.MEMBER])
    def test_matching_email_creates_membership(self, client, user, organization, role):
        invite = make_org_invite(organization, user.email, role=role)
        client.force_login(user)

        # follow the redirect: a non-admin invitee must not dead-end on the admin-only org home
        response = client.get(self._url(organization.slug, invite.token), follow=True)

        assert response.status_code == 200
        assert response.redirect_chain[-1][0] == reverse("opportunity:list", args=(organization.slug,))
        invite.refresh_from_db()
        assert invite.status == OrganizationInvite.Status.accepted
        membership = UserOrganizationMembership.objects.get(user=user, organization=organization)
        assert membership.role == role

    def test_mismatched_email_is_rejected(self, client, org_user_member, organization):
        invite = make_org_invite(organization, "stranger@example.com")
        client.force_login(org_user_member)

        response = client.get(self._url(organization.slug, invite.token))

        assert response.status_code == 302
        assert response.url == reverse("home")
        invite.refresh_from_db()
        assert invite.status == OrganizationInvite.Status.invited
        messages = list(get_messages(response.wsgi_request))
        assert "stranger@example.com" in str(messages[0])

    def test_already_accepted_shows_info(self, client, user, organization):
        invite = make_org_invite(organization, user.email, status=OrganizationInvite.Status.accepted)
        client.force_login(user)

        response = client.get(self._url(organization.slug, invite.token))

        assert response.status_code == 302
        assert response.url == reverse("home")
        messages = list(get_messages(response.wsgi_request))
        assert "already been accepted" in str(messages[0])

    def test_invalid_token_returns_404(self, client, user, organization):
        client.force_login(user)

        response = client.get(self._url(organization.slug, uuid4()))

        assert response.status_code == 404

    def test_expired_invite_is_rejected(self, client, user, organization):
        invite = make_org_invite(organization, user.email)
        expire_invite(invite)
        client.force_login(user)

        response = client.get(self._url(organization.slug, invite.token))

        assert response.status_code == 302
        assert response.url == reverse("home")
        invite.refresh_from_db()
        assert invite.status == OrganizationInvite.Status.expired
        assert not UserOrganizationMembership.objects.filter(user=user, organization=organization).exists()
        messages = list(get_messages(response.wsgi_request))
        assert "expired" in str(messages[0]).lower()

    def test_new_user_get_renders_accept_page(self, client, organization):
        invite = make_org_invite(organization, "brandnew@example.com")

        response = client.get(self._url(organization.slug, invite.token))

        assert response.status_code == 200
        assert b"brandnew@example.com" in response.content
        assert b"Create account" in response.content

    def test_new_user_sets_password_and_joins(self, client, organization):
        invite = make_org_invite(organization, "brandnew@example.com", role=UserOrganizationMembership.Role.MEMBER)

        response = client.post(
            self._url(organization.slug, invite.token),
            data={"password1": "Str0ngPass!23", "password2": "Str0ngPass!23", "agree": "on"},
            follow=True,
        )

        assert response.status_code == 200
        assert response.redirect_chain[-1][0] == reverse("opportunity:list", args=(organization.slug,))
        user = User.objects.get(email="brandnew@example.com")
        # invite link proves ownership -> the account is created already verified (no confirmation step)
        assert EmailAddress.objects.get(user=user, email="brandnew@example.com").verified
        assert response.wsgi_request.user == user  # logged in
        invite.refresh_from_db()
        assert invite.status == OrganizationInvite.Status.accepted
        assert UserOrganizationMembership.objects.filter(
            user=user, organization=organization, role=UserOrganizationMembership.Role.MEMBER
        ).exists()

    def test_new_user_password_mismatch_creates_nothing(self, client, organization):
        invite = make_org_invite(organization, "brandnew@example.com")

        response = client.post(
            self._url(organization.slug, invite.token),
            data={"password1": "Str0ngPass!23", "password2": "different", "agree": "on"},
        )

        assert response.status_code == 200  # re-renders the form
        assert not User.objects.filter(email="brandnew@example.com").exists()

    def test_new_user_without_agreement_is_rejected(self, client, organization):
        invite = make_org_invite(organization, "brandnew@example.com")

        response = client.post(
            self._url(organization.slug, invite.token),
            data={"password1": "Str0ngPass!23", "password2": "Str0ngPass!23"},  # no "agree"
        )

        assert response.status_code == 200  # re-renders with a server-side error, no bypass
        assert not User.objects.filter(email="brandnew@example.com").exists()
        invite.refresh_from_db()
        assert invite.status == OrganizationInvite.Status.invited

    def test_existing_account_is_sent_to_login(self, client, organization):
        UserFactory(email="hasaccount@example.com")
        invite = make_org_invite(organization, "hasaccount@example.com")

        response = client.get(self._url(organization.slug, invite.token))

        assert response.status_code == 302
        assert reverse("account_login") in response.url
        assert "next=" in response.url
        # invite context must not stick in the session (would leak onto a later login)
        assert "pending_invite" not in client.session

    def test_username_matching_existing_user_still_creates_account(self, client, organization):
        # a pre-existing account already uses this email string as its username
        User.objects.create_user(username="collide@example.com", email="different@example.com")
        invite = make_org_invite(organization, "collide@example.com")

        response = client.post(
            self._url(organization.slug, invite.token),
            data={"password1": "Str0ngPass!23", "password2": "Str0ngPass!23", "agree": "on"},
            follow=True,
        )

        assert response.status_code == 200  # no 500, no collision
        new_user = User.objects.get(email="collide@example.com")
        assert new_user.username != "collide@example.com"  # allauth generated a bounded, unique username
        invite.refresh_from_db()
        assert invite.status == OrganizationInvite.Status.accepted
        assert UserOrganizationMembership.objects.filter(user=new_user, organization=organization).exists()


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

    def test_pending_invites_are_listed(self, client, org_user_admin, organization):
        make_org_invite(organization, "pending@example.com")
        client.force_login(org_user_admin)
        response = client.get(self._url(organization.slug))
        assert response.status_code == 200
        assert b"pending@example.com" in response.content

    def test_expired_invites_hidden_from_pending_list(self, client, org_user_admin, organization):
        make_org_invite(organization, "fresh@example.com")
        expire_invite(make_org_invite(organization, "old@example.com"))
        client.force_login(org_user_admin)
        response = client.get(self._url(organization.slug))
        assert b"fresh@example.com" in response.content
        assert b"old@example.com" not in response.content


@pytest.mark.django_db
class TestAddMembersInviteView:
    @staticmethod
    def _url(org_slug):
        return reverse("organization:add_members", args=(org_slug,))

    @patch("commcare_connect.organization.views.send_org_invite")
    def test_admin_invites_new_email_creates_invite(
        self, send_mock, client, org_user_admin, organization, django_capture_on_commit_callbacks
    ):
        client.force_login(org_user_admin)
        with django_capture_on_commit_callbacks(execute=True):
            response = client.post(
                self._url(organization.slug), data={"email": "newperson@example.com", "role": "member"}
            )

        assert response.status_code == 302
        invite = OrganizationInvite.objects.get(organization=organization, email="newperson@example.com")
        assert invite.status == OrganizationInvite.Status.invited
        assert invite.invited_by == org_user_admin
        send_mock.assert_called_once_with(invite_id=invite.pk, host_user_id=org_user_admin.pk)

    @patch("commcare_connect.organization.views.send_org_invite")
    def test_existing_member_email_is_rejected(self, send_mock, client, org_user_admin, org_user_member, organization):
        client.force_login(org_user_admin)
        response = client.post(self._url(organization.slug), data={"email": org_user_member.email, "role": "member"})

        assert response.status_code == 302
        assert not OrganizationInvite.objects.filter(email=org_user_member.email).exists()
        send_mock.assert_not_called()
        messages = list(get_messages(response.wsgi_request))
        assert any("already belongs" in str(m) for m in messages)

    def test_non_admin_cannot_invite(self, client, org_user_member, organization):
        client.force_login(org_user_member)
        response = client.post(self._url(organization.slug), data={"email": "x@example.com", "role": "member"})
        assert response.status_code == 404

    @patch("commcare_connect.organization.views.send_org_invite")
    def test_admin_without_email_can_invite(
        self, send_mock, client, org_user_admin, organization, django_capture_on_commit_callbacks
    ):
        # an admin provisioned without an email (User.email is nullable) must not 500 on invite
        org_user_admin.email = None
        org_user_admin.save()
        client.force_login(org_user_admin)

        with django_capture_on_commit_callbacks(execute=True):
            response = client.post(
                self._url(organization.slug), data={"email": "newbie@example.com", "role": "member"}
            )

        assert response.status_code == 302
        invite = OrganizationInvite.objects.get(organization=organization, email="newbie@example.com")
        assert invite.created_by == ""
        send_mock.assert_called_once()

    @patch("commcare_connect.organization.views.send_org_invite")
    def test_reinvite_after_expiry_creates_new_invite(
        self, send_mock, client, org_user_admin, organization, django_capture_on_commit_callbacks
    ):
        old = make_org_invite(organization, "again@example.com")
        expire_invite(old)
        client.force_login(org_user_admin)

        with django_capture_on_commit_callbacks(execute=True):
            response = client.post(self._url(organization.slug), data={"email": "again@example.com", "role": "member"})

        assert response.status_code == 302
        old.refresh_from_db()
        assert old.status == OrganizationInvite.Status.expired  # stale invite retired
        assert (
            OrganizationInvite.objects.filter(
                organization=organization, email="again@example.com", status=OrganizationInvite.Status.invited
            ).count()
            == 1
        )
        send_mock.assert_called_once()


@pytest.mark.django_db
class TestRevokeInviteView:
    @staticmethod
    def _url(org_slug):
        return reverse("organization:revoke_invite", args=(org_slug,))

    def test_admin_can_revoke(self, client, org_user_admin, organization):
        invite = make_org_invite(organization, "revoke-me@example.com")
        client.force_login(org_user_admin)

        response = client.post(self._url(organization.slug), data={"invite_id": invite.pk})

        assert response.status_code == 302
        assert not OrganizationInvite.objects.filter(pk=invite.pk).exists()

    def test_non_admin_cannot_revoke(self, client, org_user_member, organization):
        invite = make_org_invite(organization, "revoke-me@example.com")
        client.force_login(org_user_member)

        response = client.post(self._url(organization.slug), data={"invite_id": invite.pk})

        assert response.status_code == 404
        assert OrganizationInvite.objects.filter(pk=invite.pk).exists()


@pytest.mark.django_db
class TestOrganizationInviteForm:
    def test_new_email_is_valid(self, organization):
        form = OrganizationInviteForm(
            data={"email": "brand-new@example.com", "role": "member"}, organization=organization
        )
        assert form.is_valid(), form.errors

    def test_existing_member_is_invalid(self, organization, org_user_member):
        form = OrganizationInviteForm(
            data={"email": org_user_member.email, "role": "member"}, organization=organization
        )
        assert not form.is_valid()
        assert "already belongs" in str(form.errors["email"])

    def test_duplicate_pending_invite_is_invalid(self, organization):
        make_org_invite(organization, "dup@example.com")
        form = OrganizationInviteForm(data={"email": "dup@example.com", "role": "member"}, organization=organization)
        assert not form.is_valid()
        assert "pending invite" in str(form.errors["email"])
