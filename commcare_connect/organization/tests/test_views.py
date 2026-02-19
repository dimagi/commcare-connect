import pytest
from django.contrib.auth.models import Permission
from django.contrib.messages import get_messages
from django.urls import reverse

from commcare_connect.organization.models import LLOEntity, Organization, UserOrganizationMembership


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

        assert response.status_code == 200
        organization.refresh_from_db()
        assert organization.program_manager is False

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

        assert response.status_code == 200
        organization.refresh_from_db()
        assert organization.program_manager is True


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
                "name": str(organization.pk),
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
                "name": org_name,
                "llo_entity": str(existing_llo.pk),
            },
        )

        assert response.status_code == 302
        org = Organization.objects.get(name=org_name)
        assert response.url == reverse("opportunity:list", args=(org.slug,))
        membership = UserOrganizationMembership.objects.get(user=user, organization=org)
        assert membership.role == UserOrganizationMembership.Role.ADMIN
