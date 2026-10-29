import pytest
from django.urls import reverse

from commcare_connect.organization.models import OrganizationInvite, UserOrganizationMembership
from commcare_connect.users.models import User
from commcare_connect.users.tests.factories import MembershipFactory, OrganizationInviteFactory

ARCHIVED_TEMPLATE = "organization/organization_archived.html"


def _archive(organization):
    organization.is_archived = True
    organization.save(update_fields=["is_archived"])


def _get_as(client, user, url_name, organization):
    client.force_login(user)
    return client.get(reverse(url_name, args=(organization.slug,)))


@pytest.mark.django_db
class TestArchivedOrganizationPages:
    @pytest.mark.parametrize("url_name", ["opportunity:list", "organization:home"])
    def test_pages_of_an_archived_organization_say_it_no_longer_exists(
        self, client, organization, org_user_member, url_name
    ):
        _archive(organization)

        response = _get_as(client, org_user_member, url_name, organization)

        assert response.status_code == 404
        assert "no longer exists" in response.content.decode()
        assert ARCHIVED_TEMPLATE in [template.name for template in response.templates]

    def test_invite_link_to_an_archived_organization_says_it_no_longer_exists(self, client, organization):
        invite = OrganizationInviteFactory(organization=organization, email="invitee@example.com")
        _archive(organization)

        response = client.post(
            reverse("organization:accept_invite", args=(organization.slug, invite.token)),
            {"new_password1": "a-Strong-passw0rd", "new_password2": "a-Strong-passw0rd", "agree": "on"},
        )

        assert response.status_code == 404
        assert ARCHIVED_TEMPLATE in [template.name for template in response.templates]
        assert not User.objects.filter(email="invitee@example.com").exists()
        assert not UserOrganizationMembership.objects.filter(organization=organization, user__email=invite.email)
        invite.refresh_from_db()
        assert invite.status == OrganizationInvite.Status.INVITED

    def test_pages_of_an_active_organization_are_unaffected(self, client, organization, org_user_member):
        response = _get_as(client, org_user_member, "opportunity:list", organization)

        assert response.status_code == 200

    @pytest.mark.parametrize("has_other_organization", [True, False])
    def test_page_offers_the_users_other_organizations(
        self, client, organization, org_user_member, has_other_organization
    ):
        if has_other_organization:
            MembershipFactory(user=org_user_member)
        _archive(organization)

        response = _get_as(client, org_user_member, "opportunity:list", organization)

        assert response.context["has_active_organization"] == has_other_organization
