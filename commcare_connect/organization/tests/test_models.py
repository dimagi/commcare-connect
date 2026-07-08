from datetime import timedelta

import pytest
from django.db import IntegrityError
from django.utils import timezone

from commcare_connect.organization.models import LLOEntity, OrganizationInvite, UserOrganizationMembership
from commcare_connect.users.tests.factories import (
    MembershipFactory,
    OrganizationFactory,
    OrganizationInviteFactory,
    UserFactory,
)


class TestLLOEntity:
    def test_str_without_short_name(self):
        entity = LLOEntity(name="World Health Organization")
        assert str(entity) == "World Health Organization"

    def test_str_with_short_name(self):
        entity = LLOEntity(name="World Health Organization", short_name="WHO")
        assert str(entity) == "World Health Organization (WHO)"

    @pytest.mark.django_db
    def test_name_must_be_unique(self):
        LLOEntity.objects.create(name="Unique LLO")
        with pytest.raises(IntegrityError):
            LLOEntity.objects.create(name="Unique LLO")


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

    def test_is_program_manager_admin_in_pm_org(self, program_manager_org_user_admin, program_manager_org):
        membership = program_manager_org.memberships.get(user=program_manager_org_user_admin)
        assert membership.is_program_manager

    def test_is_program_manager_member_in_pm_org(self, program_manager_org_user_member, program_manager_org):
        membership = program_manager_org.memberships.get(user=program_manager_org_user_member)
        assert not membership.is_program_manager


@pytest.mark.django_db
class TestOrganizationInvite:
    def test_not_expired_when_freshly_created(self, organization):
        invite = OrganizationInviteFactory(organization=organization)
        assert not invite.is_expired

    def test_expired_after_expiry_window(self, organization):
        invite = OrganizationInviteFactory(organization=organization)
        OrganizationInvite.objects.filter(pk=invite.pk).update(
            date_created=timezone.now() - timedelta(days=OrganizationInvite.EXPIRY_DAYS + 1)
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

    def test_retire_expired_frees_up_slot(self, organization):
        invite = OrganizationInviteFactory(organization=organization, email="lapsed@example.com")
        OrganizationInvite.objects.filter(pk=invite.pk).update(
            date_created=timezone.now() - timedelta(days=OrganizationInvite.EXPIRY_DAYS + 1)
        )

        OrganizationInvite.retire_expired(organization, "lapsed@example.com")

        invite.refresh_from_db()
        assert invite.status == OrganizationInvite.Status.EXPIRED
        # Now a new pending invite for the same email can be created without violating the constraint.
        OrganizationInviteFactory(organization=organization, email="lapsed@example.com")

    def test_accept_creates_membership_and_marks_accepted(self, organization):
        invite = OrganizationInviteFactory(organization=organization, role=UserOrganizationMembership.Role.ADMIN)
        user = UserFactory(email=invite.email)

        membership = invite.accept(user)

        assert membership.organization == organization
        assert membership.user == user
        assert membership.role == UserOrganizationMembership.Role.ADMIN
        invite.refresh_from_db()
        assert invite.status == OrganizationInvite.Status.ACCEPTED
