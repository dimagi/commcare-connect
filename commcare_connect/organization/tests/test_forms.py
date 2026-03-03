import pytest
from django.contrib.auth.models import Permission
from django.test import Client
from django.urls import reverse

from commcare_connect.organization.forms import OrganizationChangeForm
from commcare_connect.organization.models import LLOEntity, Organization
from commcare_connect.users.models import User
from commcare_connect.users.tests.factories import UserFactory
from commcare_connect.utils.permission_const import WORKSPACE_ENTITY_MANAGEMENT_ACCESS


class TestAddMembersView:
    @pytest.fixture(autouse=True)
    def setup(self, organization: Organization, client: Client):
        self.url = reverse("organization:add_members", kwargs={"org_slug": organization.slug})
        self.user = organization.memberships.filter(role="admin").first().user
        self.client = client
        client.force_login(self.user)

    @pytest.mark.django_db
    @pytest.mark.parametrize(
        "email, role, expected_status_code, create_user, expected_role, should_exist",
        [
            ("testformember@example.com", "member", 302, True, "member", True),
            ("testforadmin@example.com", "admin", 302, True, "admin", True),
            ("nonexistent@example.com", "member", 302, False, None, False),
            ("existing@example.com", "admin", 302, True, "member", True),
        ],
    )
    def test_add_member(
        self,
        email,
        role,
        expected_status_code,
        create_user,
        expected_role,
        should_exist,
        organization,
    ):
        if create_user:
            user = UserFactory(email=email)

            if email == "existing@example.com":
                organization.members.add(user, through_defaults={"role": expected_role})

        data = {"email": email, "role": role}
        response = self.client.post(self.url, data)

        membership_filter = {"user__email": email}

        assert response.status_code == expected_status_code
        membership_exists = organization.memberships.filter(**membership_filter).exists()
        assert membership_exists == should_exist

        if should_exist and expected_role:
            membership = organization.memberships.get(**membership_filter)
            assert membership.role == expected_role


@pytest.mark.django_db
class TestOrganizationChangeForm:
    def test_update_name(self, organization: Organization, user: User):
        form = OrganizationChangeForm(data={"name": "New Name"}, user=user, instance=organization)
        assert form.is_valid()
        form.save()
        organization.refresh_from_db()
        assert organization.name == "New Name"

    @pytest.mark.parametrize(
        "permission, program_manager",
        [
            (None, False),
            (None, True),
            (WORKSPACE_ENTITY_MANAGEMENT_ACCESS, False),
            (WORKSPACE_ENTITY_MANAGEMENT_ACCESS, True),
        ],
    )
    def test_update_program_manager_without_permission(
        self, organization: Organization, user: User, permission, program_manager
    ):
        if permission is not None:
            app_label, codename = WORKSPACE_ENTITY_MANAGEMENT_ACCESS.split(".")
            perm = Permission.objects.get(codename=codename, content_type__app_label=app_label)
            user.user_permissions.add(perm)

        user = User.objects.get(pk=user.pk)
        llo_entity = LLOEntity.objects.create(name="Test LLO")

        organization.program_manager = program_manager
        organization.save()
        form = OrganizationChangeForm(
            data={"name": organization.name, "llo_entity": llo_entity.pk},
            user=user,
            instance=organization,
        )
        assert form.is_valid(), form.errors
        form.save()
        organization.refresh_from_db()
        if permission is None:
            assert organization.llo_entity is None
        else:
            assert organization.llo_entity == llo_entity

    @pytest.mark.parametrize("permission", [None, WORKSPACE_ENTITY_MANAGEMENT_ACCESS])
    def test_create_llo_entity(self, organization: Organization, user: User, permission):
        if permission is not None:
            app_label, codename = permission.split(".")
            perm = Permission.objects.get(codename=codename, content_type__app_label=app_label)
            user.user_permissions.add(perm)

        user.refresh_from_db()

        organization.save()

        assert LLOEntity.objects.count() == 0
        form = OrganizationChangeForm(
            data={"name": organization.name, "llo_entity": "New LLO Entity"},
            user=user,
            instance=organization,
        )
        assert form.is_valid(), form.errors
        form.save()
        organization.refresh_from_db()
        if permission is None:
            assert organization.llo_entity is None
        else:
            assert organization.llo_entity is not None
            assert organization.llo_entity.name == "New LLO Entity"
            assert LLOEntity.objects.count() == 1
