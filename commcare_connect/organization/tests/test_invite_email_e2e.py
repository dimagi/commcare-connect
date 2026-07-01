"""End-to-end test of the organization invite email.

Unlike the unit tests (which mock send_mail_async), this drives the whole chain:
send the real invite through the view -> capture the real email -> pull the
accept link out of the email body -> follow that exact link as the invitee ->
assert they end up a member. It is the template for e2e-testing any app email.
"""

import pytest
from django.urls import reverse

from commcare_connect.organization.models import OrganizationInvite, UserOrganizationMembership
from commcare_connect.users.tests.factories import UserFactory
from commcare_connect.utils.tests.email import get_email_link, get_sole_email, tasks_run_eagerly


@pytest.mark.django_db
class TestOrgInviteEmailEndToEnd:
    def test_emailed_link_makes_the_invitee_a_member(
        self, client, org_user_admin, organization, django_capture_on_commit_callbacks
    ):
        invitee_email = "e2e-invitee@example.com"

        # 1. Admin sends the invite through the real view (fires send_org_invite on commit).
        client.force_login(org_user_admin)
        with tasks_run_eagerly(), django_capture_on_commit_callbacks(execute=True):
            client.post(
                reverse("organization:add_members", args=(organization.slug,)),
                data={"email": invitee_email, "role": "member"},
            )

        # 2. A real email actually went out to the invitee.
        email = get_sole_email(to=invitee_email)
        assert organization.name in email.subject

        # 3. The accept link in the email body matches the stored invite token (no hardcoded URL).
        invite = OrganizationInvite.objects.get(organization=organization, email=invitee_email)
        link = get_email_link(email, must_contain="/organization/invite/")
        assert str(invite.token) in link

        # 4. The invitee signs in with the matching email and follows the emailed link.
        invitee = UserFactory(email=invitee_email)
        client.force_login(invitee)
        response = client.get(link, follow=True)

        # 5. They land on a real page (not 404/500) and are now a member.
        assert response.status_code == 200
        invite.refresh_from_db()
        assert invite.status == OrganizationInvite.Status.accepted
        assert UserOrganizationMembership.objects.filter(
            user=invitee, organization=organization, role=UserOrganizationMembership.Role.MEMBER
        ).exists()

    def test_no_email_is_sent_when_the_invite_is_invalid(self, client, org_user_admin, organization):
        # inviting an existing member is rejected -> no email leaves the outbox
        with tasks_run_eagerly():
            client.force_login(org_user_admin)
            client.post(
                reverse("organization:add_members", args=(organization.slug,)),
                data={"email": org_user_admin.email, "role": "member"},
            )

        from django.core import mail

        assert mail.outbox == []
