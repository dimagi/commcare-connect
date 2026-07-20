from unittest.mock import patch

import pytest

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
