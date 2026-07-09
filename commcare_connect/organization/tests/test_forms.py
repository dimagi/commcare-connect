from datetime import timedelta

import pytest
from django.contrib.auth.models import Permission
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from commcare_connect.organization.forms import OrganizationChangeForm, OrganizationSelectOrCreateForm
from commcare_connect.organization.models import LLOEntity, Organization, OrganizationInvite
from commcare_connect.users.models import User
from commcare_connect.users.tests.factories import LLOEntityFactory, OrganizationInviteFactory, UserFactory
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
        "email, role, create_existing_user",
        [
            ("brandnew@example.com", "member", False),
            ("brandnewadmin@example.com", "admin", False),
            ("registered@example.com", "member", True),
        ],
    )
    def test_add_member_creates_pending_invite(self, email, role, create_existing_user, organization):
        if create_existing_user:
            UserFactory(email=email)

        response = self.client.post(self.url, {"email": email, "role": role})

        assert response.status_code == 302
        assert not organization.memberships.filter(user__email=email).exists()
        invite = OrganizationInvite.objects.get(organization=organization, email=email)
        assert invite.role == role
        assert invite.status == OrganizationInvite.Status.INVITED
        assert invite.invited_by == self.user

    @pytest.mark.django_db
    def test_invite_rejected_for_existing_member(self, organization):
        member = UserFactory(email="already-a-member@example.com")
        organization.members.add(member, through_defaults={"role": "member"})

        response = self.client.post(self.url, {"email": member.email, "role": "member"}, follow=True)

        messages = list(response.context["messages"])
        assert len(messages) == 1
        assert messages[0].level_tag == "error"
        assert "already a member" in str(messages[0])
        assert not OrganizationInvite.objects.filter(organization=organization, email=member.email).exists()

    @pytest.mark.django_db
    def test_invite_rejected_when_pending_invite_already_exists(self, organization):
        existing_invite = OrganizationInviteFactory(organization=organization, email="pending@example.com")

        response = self.client.post(self.url, {"email": existing_invite.email, "role": "admin"}, follow=True)

        messages = list(response.context["messages"])
        assert len(messages) == 1
        assert messages[0].level_tag == "error"
        assert "already been sent" in str(messages[0])
        assert OrganizationInvite.objects.filter(organization=organization, email=existing_invite.email).count() == 1

    @pytest.mark.django_db
    def test_reinvite_after_expiry_resets_existing_invite(self, organization):
        expired_invite = OrganizationInviteFactory(organization=organization, email="lapsed@example.com")
        old_token = expired_invite.token
        OrganizationInvite.objects.filter(pk=expired_invite.pk).update(
            date_modified=timezone.now() - timedelta(days=OrganizationInvite.EXPIRY_DAYS + 1)
        )

        response = self.client.post(self.url, {"email": "lapsed@example.com", "role": "member"}, follow=True)

        messages = list(response.context["messages"])
        assert messages[0].level_tag == "success"
        expired_invite.refresh_from_db()
        assert expired_invite.status == OrganizationInvite.Status.INVITED
        assert expired_invite.token != old_token
        assert OrganizationInvite.objects.filter(organization=organization, email="lapsed@example.com").count() == 1

    @pytest.mark.django_db
    def test_valid_invite_shows_success_message(self):
        response = self.client.post(self.url, {"email": "newmember@example.com", "role": "member"}, follow=True)

        messages = list(response.context["messages"])
        assert len(messages) == 1
        assert messages[0].level_tag == "success"


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
