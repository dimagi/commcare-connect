from datetime import timedelta

import pytest
from django import forms
from django.contrib.auth.models import Permission
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from commcare_connect.organization.forms import (
    OrganizationChangeForm,
    OrganizationProfileForm,
)
from commcare_connect.organization.models import Organization, OrganizationInvite
from commcare_connect.users.models import User
from commcare_connect.users.tests.factories import OrganizationInviteFactory, UserFactory
from commcare_connect.utils.permission_const import ORG_MANAGEMENT_SETTINGS_ACCESS


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
    def _grant_org_settings_perm(self, user: User) -> User:
        app_label, codename = ORG_MANAGEMENT_SETTINGS_ACCESS.split(".")
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

    @pytest.mark.parametrize("program_manager", [False, True])
    def test_program_manager_field_hidden_without_permission(
        self, organization: Organization, user: User, program_manager
    ):
        organization.program_manager = program_manager
        organization.save()

        form = OrganizationChangeForm(
            data={"name": organization.name, "program_manager": "on"},
            user=user,
            instance=organization,
        )
        assert "program_manager" not in form.fields
        assert form.is_valid(), form.errors
        form.save()
        organization.refresh_from_db()
        assert organization.program_manager == program_manager

    def test_program_manager_updates_with_permission(self, organization: Organization, user: User):
        user = self._grant_org_settings_perm(user)
        organization.program_manager = False
        organization.save()

        form = OrganizationChangeForm(
            data={"name": organization.name, "program_manager": "on"},
            user=user,
            instance=organization,
        )
        assert form.is_valid(), form.errors
        form.save()
        organization.refresh_from_db()
        assert organization.program_manager


@pytest.mark.django_db
class TestOrganizationProfileForm:
    @staticmethod
    def _data(**overrides):
        return {"name": "Profile Org", **overrides}

    def test_contact_emails_are_normalized(self, organization: Organization):
        form = OrganizationProfileForm(
            data=self._data(contact_emails="  one@example.com \n\n two@example.com  "),
            instance=organization,
        )
        assert form.is_valid(), form.errors
        assert form.cleaned_data["contact_emails"] == "one@example.com\ntwo@example.com"

    def test_invalid_contact_emails_are_rejected(self, organization: Organization):
        form = OrganizationProfileForm(
            data=self._data(contact_emails="ok@example.com\nnot-an-email"),
            instance=organization,
        )
        assert not form.is_valid()
        assert "not-an-email" in form.errors["contact_emails"][0]

    def test_eoi_links_are_normalized(self, organization: Organization):
        form = OrganizationProfileForm(
            data=self._data(eoi_links=" https://example.com/eoi \n\n https://example.org/eoi "),
            instance=organization,
        )
        assert form.is_valid(), form.errors
        assert form.cleaned_data["eoi_links"] == "https://example.com/eoi\nhttps://example.org/eoi"

    def test_invalid_eoi_links_are_rejected(self, organization: Organization):
        form = OrganizationProfileForm(
            data=self._data(eoi_links="https://example.com/eoi\nnope"),
            instance=organization,
        )
        assert not form.is_valid()
        assert "nope" in form.errors["eoi_links"][0]

    @pytest.mark.parametrize("year", [1799, 3000])
    def test_year_of_establishment_out_of_range_is_rejected(self, organization: Organization, year):
        form = OrganizationProfileForm(data=self._data(year_of_establishment=year), instance=organization)
        assert not form.is_valid()
        assert "year_of_establishment" in form.errors

    def test_profile_is_saved_to_the_organization(self, organization: Organization):
        form = OrganizationProfileForm(
            data=self._data(
                short_name="PO",
                year_of_establishment=2005,
                contact_emails="one@example.com",
                eoi_links="https://example.com/eoi",
                website="https://example.com",
                regions="North",
            ),
            instance=organization,
        )
        assert form.is_valid(), form.errors
        form.save()

        organization.refresh_from_db()
        assert organization.name == "Profile Org"
        assert organization.short_name == "PO"
        assert organization.year_of_establishment == 2005
        assert organization.contact_emails == "one@example.com"
        assert organization.eoi_links == "https://example.com/eoi"

    def test_name_is_a_text_input(self):
        # Workspaces are typed in, not picked from a list of existing ones.
        widget = OrganizationProfileForm().fields["name"].widget
        assert isinstance(widget, forms.TextInput)

    @pytest.mark.parametrize("field_name", ["countries", "primary_sectors"])
    def test_multi_select_is_a_tomselect_widget(self, field_name):
        # tomselect.js binds on the [data-tomselect] attribute at DOMContentLoaded, and
        # picks up multi-select from the `multiple` attribute SelectMultiple renders.
        widget = OrganizationProfileForm().fields[field_name].widget

        assert isinstance(widget, forms.SelectMultiple)
        assert widget.attrs.get("data-tomselect") == "1"

    def test_new_workspace_is_created_with_a_slug(self):
        form = OrganizationProfileForm(data=self._data(name="Brand New Workspace"))

        assert form.is_valid(), form.errors
        org = form.save()

        assert org.pk is not None
        assert org.slug == "brand-new-workspace"

    def test_name_is_required(self):
        form = OrganizationProfileForm(data=self._data(name=""))

        assert not form.is_valid()
        assert "name" in form.errors

    @pytest.mark.parametrize("name", ["Taken Name", "taken name"])
    def test_duplicate_name_is_rejected(self, name):
        Organization.objects.create(name="Taken Name")

        form = OrganizationProfileForm(data=self._data(name=name))

        assert not form.is_valid()
        assert "name" in form.errors

    def test_own_name_is_not_a_duplicate_when_editing(self, organization: Organization):
        form = OrganizationProfileForm(data=self._data(name=organization.name), instance=organization)

        assert form.is_valid(), form.errors
