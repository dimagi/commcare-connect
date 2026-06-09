import pytest
from django.db import IntegrityError

from commcare_connect.organization.models import LLOEntity, UserOrganizationMembership
from commcare_connect.users.tests.factories import MembershipFactory, OrganizationFactory


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
