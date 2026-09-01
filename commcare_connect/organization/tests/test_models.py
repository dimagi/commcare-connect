from datetime import timedelta

import pytest
from django.contrib.auth.models import Permission
from django.db import IntegrityError
from django.utils import timezone

from commcare_connect.organization.models import (
    Organization,
    OrganizationInvite,
    UserOrganizationMembership,
)
from commcare_connect.users.models import User
from commcare_connect.users.tests.factories import (
    MembershipFactory,
    OrganizationFactory,
    OrganizationInviteFactory,
    UserFactory,
)


def _user_with_privilege(privilege: str | None, own_org: Organization) -> User:
    user = UserFactory(is_superuser=privilege == "superuser")
    if privilege == "member":
        MembershipFactory(user=user, organization=own_org)
    if privilege == "permission":
        user.user_permissions.add(Permission.objects.get(codename="workspace_entity_management_access"))
        user = User.objects.get(pk=user.pk)  # drop the cached permissions
    return user


@pytest.mark.django_db
class TestOrganization:
    def test_slug_generated_from_name_on_create(self):
        org = OrganizationFactory(name="Health Workers Org")
        assert org.slug == "health-workers-org"

    def test_slug_not_overwritten_on_update(self):
        org = OrganizationFactory(name="Health Workers Org")
        original_slug = org.slug
        org.name = "Renamed Org"
        org.save()
        assert org.slug == original_slug

    def test_get_member_emails_returns_all(self, organization, org_user_admin, org_user_member):
        emails = organization.get_member_emails()
        assert sorted(emails) == sorted([org_user_admin.email, org_user_member.email])

    @pytest.mark.parametrize(
        "exclude_viewer,expect_viewer",
        [(False, True), (True, False)],
    )
    def test_get_member_emails_viewer_exclusion(
        self, organization, org_user_admin, org_user_member, exclude_viewer, expect_viewer
    ):
        viewer = UserFactory(email="viewer@example.com")
        MembershipFactory(organization=organization, user=viewer, role=UserOrganizationMembership.Role.VIEWER)

        emails = organization.get_member_emails(exclude_viewer=exclude_viewer)

        expected = [org_user_admin.email, org_user_member.email]
        if expect_viewer:
            expected.append(viewer.email)
        assert sorted(emails) == sorted(expected)

    @pytest.mark.parametrize(
        "privilege, visible",
        [
            (None, set()),
            ("member", {"own"}),
            ("permission", {"own", "other"}),
            ("superuser", {"own", "other"}),
        ],
    )
    def test_visible_to(self, privilege, visible):
        orgs = {"own": OrganizationFactory(), "other": OrganizationFactory()}
        user = _user_with_privilege(privilege, orgs["own"])

        assert set(Organization.visible_to(user)) == {orgs[key] for key in visible}


@pytest.mark.django_db
class TestUserOrganizationMembership:
    def test_admin_role_is_admin(self, org_user_admin, organization):
        membership = organization.memberships.get(user=org_user_admin)
        assert membership.is_admin

    def test_member_role_is_not_admin(self, org_user_member, organization):
        membership = organization.memberships.get(user=org_user_member)
        assert not membership.is_admin

    def test_viewer_role_is_viewer(self):
        membership = MembershipFactory(role=UserOrganizationMembership.Role.VIEWER)
        assert membership.is_viewer


@pytest.mark.django_db
class TestOrganizationInvite:
    def test_not_expired_when_freshly_created(self, organization):
        invite = OrganizationInviteFactory(organization=organization)
        assert not invite.is_expired

    def test_expired_after_expiry_window(self, organization):
        invite = OrganizationInviteFactory(organization=organization)
        OrganizationInvite.objects.filter(pk=invite.pk).update(
            date_modified=timezone.now() - timedelta(days=OrganizationInvite.EXPIRY_DAYS + 1)
        )
        invite.refresh_from_db()
        assert invite.is_expired

    def test_accepted_invite_is_never_expired(self, organization):
        invite = OrganizationInviteFactory(organization=organization, status=OrganizationInvite.Status.ACCEPTED)
        OrganizationInvite.objects.filter(pk=invite.pk).update(
            date_created=timezone.now() - timedelta(days=OrganizationInvite.EXPIRY_DAYS + 1)
        )
        invite.refresh_from_db()
        assert not invite.is_expired

    def test_only_one_pending_invite_per_email_allowed(self, organization):
        OrganizationInviteFactory(organization=organization, email="dup@example.com")
        with pytest.raises(IntegrityError):
            OrganizationInviteFactory(organization=organization, email="dup@example.com")

    def test_send_invite_resets_lapsed_invite(self, organization):
        invite = OrganizationInviteFactory(organization=organization, email="lapsed@example.com")
        old_token = invite.token
        OrganizationInvite.objects.filter(pk=invite.pk).update(
            date_modified=timezone.now() - timedelta(days=OrganizationInvite.EXPIRY_DAYS + 1)
        )
        admin = UserFactory(email="admin@example.com")

        reinvited = OrganizationInvite.send_invite(
            organization=organization,
            email="lapsed@example.com",
            role=UserOrganizationMembership.Role.MEMBER,
            invited_by=admin,
        )

        assert reinvited.pk == invite.pk
        assert reinvited.status == OrganizationInvite.Status.INVITED
        assert reinvited.token != old_token
        assert not reinvited.is_expired

    def test_send_invite_preserves_original_created_by_on_reinvite(self, organization):
        invite = OrganizationInviteFactory(organization=organization, email="lapsed@example.com")
        original_created_by = invite.created_by
        admin = UserFactory(email="admin@example.com")

        reinvited = OrganizationInvite.send_invite(
            organization=organization,
            email="lapsed@example.com",
            role=UserOrganizationMembership.Role.MEMBER,
            invited_by=admin,
        )

        assert reinvited.created_by == original_created_by
        assert reinvited.modified_by == admin.email

    def test_accept_creates_membership_and_marks_accepted(self, organization):
        invite = OrganizationInviteFactory(organization=organization, role=UserOrganizationMembership.Role.ADMIN)
        user = UserFactory(email=invite.email)

        membership = invite.accept(user)

        assert membership.organization == organization
        assert membership.user == user
        assert membership.role == UserOrganizationMembership.Role.ADMIN
        invite.refresh_from_db()
        assert invite.status == OrganizationInvite.Status.ACCEPTED
