from unittest.mock import patch

import pytest

from commcare_connect.organization.models import OrganizationInvite
from commcare_connect.organization.tasks import send_org_invite


def _make_invite(organization, email="invitee@example.com"):
    return OrganizationInvite.objects.create(
        organization=organization,
        email=email,
        created_by="host@example.com",
        modified_by="host@example.com",
    )


@pytest.mark.django_db
@patch("commcare_connect.organization.tasks.send_mail_async")
class TestSendOrgInvite:
    def test_sends_email_with_correct_details(self, send_mock, user, organization):
        invite = _make_invite(organization)

        send_org_invite(invite.pk, user.pk)

        send_mock.delay.assert_called_once()
        _, kwargs = send_mock.delay.call_args
        assert user.name in kwargs["subject"]
        assert invite.organization.name in kwargs["subject"]
        assert str(invite.token) in kwargs["message"]
        assert kwargs["recipient_list"] == [invite.email]

    def test_skips_email_when_invite_has_no_email(self, send_mock, user, organization):
        invite = _make_invite(organization, email="")

        send_org_invite(invite.pk, user.pk)

        send_mock.delay.assert_not_called()
