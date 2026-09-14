from unittest.mock import patch

import pytest
from django.template.defaultfilters import date as date_filter
from django.utils.html import escape

from commcare_connect.organization.tasks import send_org_invite
from commcare_connect.users.tests.factories import OrganizationInviteFactory


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
