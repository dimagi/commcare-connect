import pytest
from django.contrib.auth.models import Permission
from django.test import Client
from django.urls import reverse

from commcare_connect.organization.forms import OrganizationChangeForm, OrganizationCreationForm
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


@pytest.mark.django_db
class TestOrganizationCreationForm:
    def test_both_llo_entity_and_org_exist(self):
        existing_llo = LLOEntity.objects.create(name="Existing LLO")
        existing_org = Organization.objects.create(name="Existing Org", llo_entity=existing_llo)

        initial_llo_count = LLOEntity.objects.count()
        initial_org_count = Organization.objects.count()

        form = OrganizationCreationForm(
            data={
                "org": str(existing_org.pk),
                "llo_entity": str(existing_llo.pk),
            }
        )

        assert form.is_valid(), form.errors
        org = form.save()

        assert LLOEntity.objects.count() == initial_llo_count
        assert Organization.objects.count() == initial_org_count

        assert org.pk == existing_org.pk
        assert org.name == "Existing Org"
        assert org.llo_entity == existing_llo

    def test_llo_entity_exists_new_org_created(self):
        existing_llo = LLOEntity.objects.create(name="Existing LLO")

        initial_llo_count = LLOEntity.objects.count()
        initial_org_count = Organization.objects.count()

        form = OrganizationCreationForm(
            data={
                "org": "New Organization",
                "llo_entity": str(existing_llo.pk),
            }
        )

        assert form.is_valid(), form.errors
        org = form.save()

        assert LLOEntity.objects.count() == initial_llo_count
        assert Organization.objects.count() == initial_org_count + 1

        assert org.pk is not None
        assert org.name == "New Organization"
        assert org.llo_entity == existing_llo

    def test_both_new_llo_entity_and_new_org_created(self):
        initial_llo_count = LLOEntity.objects.count()
        initial_org_count = Organization.objects.count()

        form = OrganizationCreationForm(
            data={
                "org": "Brand New Organization",
                "llo_entity": "Brand New LLO",
            }
        )

        assert form.is_valid(), form.errors
        org = form.save()

        assert LLOEntity.objects.count() == initial_llo_count + 1
        assert Organization.objects.count() == initial_org_count + 1

        assert org.pk is not None
        assert org.name == "Brand New Organization"
        assert org.llo_entity is not None
        assert org.llo_entity.pk is not None
        assert org.llo_entity.name == "Brand New LLO"

    def test_validation_error_mismatched_llo_entity(self):
        llo1 = LLOEntity.objects.create(name="LLO One")
        llo2 = LLOEntity.objects.create(name="LLO Two")
        existing_org = Organization.objects.create(name="Org With LLO One", llo_entity=llo1)

        form = OrganizationCreationForm(
            data={
                "org": str(existing_org.pk),
                "llo_entity": str(llo2.pk),  # Different LLO
            }
        )

        assert not form.is_valid()
        assert "llo_entity" in form.errors
        assert form.errors["llo_entity"] == [
            "Selected LLO Entity does not match the existing organization's LLO Entity."
        ]
