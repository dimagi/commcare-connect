from unittest.mock import patch

import pytest
from django.template.defaultfilters import date as date_filter
from django.utils.html import escape

from commcare_connect.organization.models import UserOrganizationMembership
from commcare_connect.organization.tasks import send_invite_accepted_notification, send_org_invite
from commcare_connect.users.tests.factories import (
    MembershipFactory,
    OrganizationFactory,
    OrganizationInviteFactory,
    UserFactory,
)


@pytest.mark.django_db
@patch("commcare_connect.organization.tasks.send_mail_async")
class TestSendOrgInvite:
    def test_sends_email_with_correct_details(self, send_mock, user, organization):
        invite = OrganizationInviteFactory(organization=organization, invited_by=user, email="invitee@example.com")

        send_org_invite(invite.pk)

        send_mock.delay.assert_called_once()
        _, kwargs = send_mock.delay.call_args
        assert user.name in kwargs["subject"]
        assert invite.organization.name in kwargs["subject"]
        assert invite.token in kwargs["message"]
        assert kwargs["recipient_list"] == [invite.email]

    def test_falls_back_to_org_name_when_inviter_is_gone(self, send_mock, organization):
        invite = OrganizationInviteFactory(organization=organization, invited_by=None, email="invitee@example.com")

        send_org_invite(invite.pk)

        send_mock.delay.assert_called_once()
        _, kwargs = send_mock.delay.call_args
        assert organization.name in kwargs["subject"]

    def test_message_includes_role_and_expiry_date(self, send_mock, user, organization):
        invite = OrganizationInviteFactory(
            organization=organization, invited_by=user, email="invitee@example.com", role="admin"
        )

        send_org_invite(invite.pk)

        _, kwargs = send_mock.delay.call_args
        assert invite.get_role_display() in kwargs["message"]
        assert date_filter(invite.expiry_date, "F j, Y H:i T") in kwargs["message"]

    def test_sends_html_alternative(self, send_mock, user, organization):
        invite = OrganizationInviteFactory(organization=organization, invited_by=user, email="invitee@example.com")

        send_org_invite(invite.pk)

        _, kwargs = send_mock.delay.call_args
        assert invite.token in kwargs["html_message"]
        assert escape(organization.name) in kwargs["html_message"]


@pytest.mark.django_db
@patch("commcare_connect.organization.tasks.send_mail_async")
class TestSendInviteAcceptedNotification:
    def test_notifies_all_admins_except_the_new_member(self, send_mock):
        organization = OrganizationFactory()
        admin_one = MembershipFactory(organization=organization, role="admin")
        admin_two = MembershipFactory(organization=organization, role="admin")
        MembershipFactory(organization=organization, role="member")
        new_admin_membership = MembershipFactory(organization=organization, role="admin")

        send_invite_accepted_notification(new_admin_membership.pk)

        send_mock.delay.assert_called_once()
        _, kwargs = send_mock.delay.call_args
        assert sorted(kwargs["recipient_list"]) == sorted([admin_one.user.email, admin_two.user.email])
        assert new_admin_membership.user.email not in kwargs["recipient_list"]

    def test_subject_and_body_include_new_member_and_org(self, send_mock):
        organization = OrganizationFactory()
        MembershipFactory(organization=organization, role="admin")
        new_member = UserFactory(email="newmember@example.com", name="New Member")
        membership = UserOrganizationMembership.objects.create(
            organization=organization, user=new_member, role="member"
        )

        send_invite_accepted_notification(membership.pk)

        _, kwargs = send_mock.delay.call_args
        assert new_member.name in kwargs["subject"]
        assert organization.name in kwargs["subject"]
        assert new_member.name in kwargs["message"]
        assert escape(organization.name) in kwargs["html_message"]

    def test_no_email_sent_when_org_has_no_other_admins(self, send_mock):
        organization = OrganizationFactory()
        new_admin_membership = MembershipFactory(organization=organization, role="admin")

        send_invite_accepted_notification(new_admin_membership.pk)

        send_mock.delay.assert_not_called()
