import pytest
from django.contrib.messages import get_messages
from django.urls import reverse
from django.utils.html import escape

from commcare_connect.opportunity.tests.factories import HQApiKeyFactory, OpportunityFactory
from commcare_connect.organization.models import UserOrganizationMembership
from commcare_connect.organization.views import MANAGING_API_KEYS_URL, api_key_removal_blocks
from commcare_connect.program.tests.factories import ProgramFactory
from commcare_connect.solicitation.models import SolicitationReviewer
from commcare_connect.solicitation.tests.factories import SolicitationReviewerFactory
from commcare_connect.users.tests.factories import UserFactory


@pytest.mark.django_db
class TestRemoveMembersView:
    def url(self, org_slug):
        return reverse("organization:remove_members", args=(org_slug,))

    def edit_url(self, organization, opportunity):
        return reverse("opportunity:edit", args=(organization.slug, opportunity.opportunity_id))

    def test_member_whose_api_key_is_in_use_blocks_the_removal(
        self, client, org_user_admin, org_user_member, organization
    ):
        """Nobody selected is removed, and the message names the opportunities whose key needs replacing.

        The names are user input inside a message that carries a link, so they must arrive escaped.
        """
        api_key = HQApiKeyFactory(user=org_user_member)
        opp_b, opp_a = (
            OpportunityFactory(name=name, program=ProgramFactory(organization=organization), api_key=api_key)
            for name in ["Opp B", "Opp <A>"]
        )
        UserOrganizationMembership.objects.create(user=UserFactory(), organization=organization)
        selected = UserOrganizationMembership.objects.filter(organization=organization).exclude(user=org_user_admin)
        membership_ids = list(selected.values_list("id", flat=True))

        messages = self._remove_members(client, org_user_admin, organization, membership_ids)

        assert messages == [
            f"{escape(org_user_member.name)} can't be removed because their HQ API key is used by these "
            f'opportunities: <a href="{self.edit_url(organization, opp_a)}" class="underline">Opp &lt;A&gt;</a>, '
            f'<a href="{self.edit_url(organization, opp_b)}" class="underline">Opp B</a>. '
            "Replace the API key on each one first. "
            f'<a href="{MANAGING_API_KEYS_URL}" target="_blank" rel="noopener" class="underline">'
            "Learn how to replace an API key</a>."
        ]
        assert selected.count() == len(membership_ids) == 2

    def test_member_without_name_or_email_is_named_by_username(self, organization):
        user = UserFactory(name="", email=None)
        membership = UserOrganizationMembership.objects.create(user=user, organization=organization)
        OpportunityFactory(program=ProgramFactory(organization=organization), api_key=HQApiKeyFactory(user=user))

        [message] = api_key_removal_blocks([membership])

        assert message.startswith(f"{user.username} can't be removed")

    def test_non_admin_cannot_access(self, client, org_user_member, organization):
        client.force_login(org_user_member)
        response = client.post(
            self.url(org_slug=organization.slug),
            data={},
        )
        assert response.status_code == 404

    def test_admin_cannot_remove_self(self, client, org_user_admin, organization):
        membership = UserOrganizationMembership.objects.get(user=org_user_admin, organization=organization)

        messages = self._remove_members(
            client=client, admin=org_user_admin, organization=organization, membership_ids=[membership.id]
        )

        assert messages == ["You cannot remove yourself from the organization."]
        assert UserOrganizationMembership.objects.filter(id=membership.id).exists()

    def test_admin_can_remove_others(self, client, org_user_admin, org_user_member, organization):
        other_membership = UserOrganizationMembership.objects.get(user=org_user_member, organization=organization)

        messages = self._remove_members(
            client=client, admin=org_user_admin, organization=organization, membership_ids=[other_membership.id]
        )

        assert messages == ["Selected members have been removed from the organization."]
        assert not UserOrganizationMembership.objects.filter(id=other_membership.id).exists()

    def test_removed_member_leaves_the_organizations_review_teams(
        self, client, org_user_admin, org_user_member, organization
    ):
        membership = UserOrganizationMembership.objects.get(user=org_user_member, organization=organization)
        SolicitationReviewerFactory(solicitation__organization=organization, user=org_user_member)
        kept = [
            SolicitationReviewerFactory(solicitation__organization=organization),  # another member
            SolicitationReviewerFactory(user=org_user_member),  # the same person, reviewing for another organization
        ]

        self._remove_members(client, org_user_admin, organization, [membership.id])

        assert list(SolicitationReviewer.objects.order_by("pk")) == kept

    def test_request_fails_when_admin_in_list(self, client, org_user_admin, org_user_member, organization):
        admin_membership = UserOrganizationMembership.objects.get(user=org_user_admin, organization=organization)
        other_membership = UserOrganizationMembership.objects.get(user=org_user_member, organization=organization)

        messages = self._remove_members(
            client=client,
            admin=org_user_admin,
            organization=organization,
            membership_ids=[admin_membership.id, other_membership.id],
        )

        assert messages == ["You cannot remove yourself from the organization."]
        assert UserOrganizationMembership.objects.filter(id=other_membership.id).exists()

    def _remove_members(self, client, admin, organization, membership_ids):
        """Post a removal as `admin` and return the messages it produced."""
        client.force_login(admin)
        response = client.post(self.url(org_slug=organization.slug), data={"membership_ids": membership_ids})
        assert response.status_code == 302
        return [str(message) for message in get_messages(response.wsgi_request)]
