import pytest
from django.contrib.auth.models import Permission
from django.test import Client
from django.urls import reverse

from commcare_connect.organization.forms import OrganizationChangeForm, OrganizationSelectOrCreateForm
from commcare_connect.organization.models import LLOEntity, Organization
from commcare_connect.users.models import User
from commcare_connect.users.tests.factories import LLOEntityFactory, UserFactory
from commcare_connect.utils.forms import TOMSELECT_NEW_ENTRY_PREFIX
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
    def _grant_entity_management_perm(self, user: User) -> User:
        app_label, codename = WORKSPACE_ENTITY_MANAGEMENT_ACCESS.split(".")
        perm = Permission.objects.get(codename=codename, content_type__app_label=app_label)
        user.user_permissions.add(perm)
        # Django caches permissions on the user instance; fetch a fresh instance to clear the cache.
        return User.objects.get(pk=user.pk)

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
            user = self._grant_entity_management_perm(user)

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
            user = self._grant_entity_management_perm(user)

        organization.save()

        assert LLOEntity.objects.count() == 0
        form = OrganizationChangeForm(
            data={
                "name": organization.name,
                "llo_entity": TOMSELECT_NEW_ENTRY_PREFIX + "New LLO Entity",
                "llo_entity_short_name": "NL",
            },
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
            assert organization.llo_entity.short_name == "NL"
            assert LLOEntity.objects.count() == 1

    def test_existing_llo_entity_short_name_not_updated(self, organization: Organization, user: User):
        user = self._grant_entity_management_perm(user)

        llo_entity = LLOEntityFactory(short_name="OLD")
        organization.llo_entity = llo_entity
        organization.save()

        form = OrganizationChangeForm(
            data={"name": organization.name, "llo_entity": llo_entity.pk, "llo_entity_short_name": "CHANGED"},
            user=user,
            instance=organization,
        )
        assert form.is_valid(), form.errors
        form.save()
        llo_entity.refresh_from_db()
        assert llo_entity.short_name == "OLD"

    def test_create_llo_entity_without_short_name_is_invalid(self, organization: Organization, user: User):
        user = self._grant_entity_management_perm(user)

        form = OrganizationChangeForm(
            data={"name": organization.name, "llo_entity": TOMSELECT_NEW_ENTRY_PREFIX + "New LLO Entity"},
            user=user,
            instance=organization,
        )
        assert not form.is_valid()
        assert "llo_entity_short_name" in form.errors


@pytest.mark.django_db
class TestOrganizationSelectOrCreateForm:
    def test_both_llo_entity_and_org_exist(self):
        existing_llo = LLOEntity.objects.create(name="Existing LLO")
        existing_org = Organization.objects.create(name="Existing Org", llo_entity=existing_llo)

        initial_llo_count = LLOEntity.objects.count()
        initial_org_count = Organization.objects.count()

        form = OrganizationSelectOrCreateForm(
            data={
                "org": str(existing_org.pk),
                "llo_entity": str(existing_llo.pk),
            }
        )

        assert form.is_valid(), form.errors
        org, is_new_org = form.save()

        assert LLOEntity.objects.count() == initial_llo_count
        assert Organization.objects.count() == initial_org_count

        assert org.pk == existing_org.pk
        assert org.name == "Existing Org"
        assert org.llo_entity == existing_llo
        assert not is_new_org

    def test_llo_entity_exists_new_org_created(self):
        existing_llo = LLOEntity.objects.create(name="Existing LLO")

        initial_llo_count = LLOEntity.objects.count()
        initial_org_count = Organization.objects.count()

        form = OrganizationSelectOrCreateForm(
            data={
                "org": TOMSELECT_NEW_ENTRY_PREFIX + "New Organization",
                "llo_entity": str(existing_llo.pk),
            }
        )

        assert form.is_valid(), form.errors
        org, is_new_org = form.save()

        assert LLOEntity.objects.count() == initial_llo_count
        assert Organization.objects.count() == initial_org_count + 1

        assert org.pk is not None
        assert org.name == "New Organization"
        assert org.llo_entity == existing_llo
        assert is_new_org

    def test_both_new_llo_entity_and_new_org_created(self):
        initial_llo_count = LLOEntity.objects.count()
        initial_org_count = Organization.objects.count()

        form = OrganizationSelectOrCreateForm(
            data={
                "org": TOMSELECT_NEW_ENTRY_PREFIX + "Brand New Organization",
                "llo_entity": TOMSELECT_NEW_ENTRY_PREFIX + "Brand New LLO",
                "llo_entity_short_name": "BNL",
            }
        )

        assert form.is_valid(), form.errors
        org, is_new_org = form.save()

        assert LLOEntity.objects.count() == initial_llo_count + 1
        assert Organization.objects.count() == initial_org_count + 1

        assert org.pk is not None
        assert org.name == "Brand New Organization"
        assert org.llo_entity is not None
        assert org.llo_entity.pk is not None
        assert org.llo_entity.name == "Brand New LLO"
        assert org.llo_entity.short_name == "BNL"
        assert is_new_org

    def test_validation_error_mismatched_llo_entity(self):
        llo1 = LLOEntity.objects.create(name="LLO One")
        llo2 = LLOEntity.objects.create(name="LLO Two")
        existing_org = Organization.objects.create(name="Org With LLO One", llo_entity=llo1)

        form = OrganizationSelectOrCreateForm(
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

    def test_create_new_llo_entity_with_short_name(self):
        form = OrganizationSelectOrCreateForm(
            data={
                "org": TOMSELECT_NEW_ENTRY_PREFIX + "New Org",
                "llo_entity": TOMSELECT_NEW_ENTRY_PREFIX + "New LLO",
                "llo_entity_short_name": "NL",
            }
        )
        assert form.is_valid(), form.errors
        org, is_new_org = form.save()
        assert org.llo_entity is not None
        assert org.llo_entity.name == "New LLO"
        assert org.llo_entity.short_name == "NL"

    def test_existing_llo_entity_short_name_not_updated(self):
        existing_llo = LLOEntityFactory(short_name="EL")
        form = OrganizationSelectOrCreateForm(
            data={
                "org": TOMSELECT_NEW_ENTRY_PREFIX + "New Org",
                "llo_entity": str(existing_llo.pk),
                "llo_entity_short_name": "CHANGED",
            }
        )
        assert form.is_valid(), form.errors
        form.save()
        existing_llo.refresh_from_db()
        assert existing_llo.short_name == "EL"

    def test_create_new_llo_entity_without_short_name_is_invalid(self):
        form = OrganizationSelectOrCreateForm(
            data={
                "org": TOMSELECT_NEW_ENTRY_PREFIX + "New Org",
                "llo_entity": TOMSELECT_NEW_ENTRY_PREFIX + "New LLO",
            }
        )
        assert not form.is_valid()
        assert "llo_entity_short_name" in form.errors
