from unittest.mock import patch

import pytest

from commcare_connect.organization.tasks import send_org_invite
from commcare_connect.users.tests.factories import MembershipFactory, UserFactory


@pytest.mark.django_db
@patch("commcare_connect.organization.tasks.send_mail_async")
class TestSendOrgInvite:
    def test_sends_email_with_correct_details(self, send_mock, user, organization):
        membership = organization.memberships.first()

        send_org_invite(membership.pk, user.pk)

        send_mock.delay.assert_called_once()
        _, kwargs = send_mock.delay.call_args
        assert user.name in kwargs["subject"]
        assert membership.organization.name in kwargs["subject"]
        assert str(membership.invite_id) in kwargs["message"]
        assert kwargs["recipient_list"] == [membership.user.email]

    def test_skips_email_when_user_has_no_email(self, send_mock, user):
        invited_user = UserFactory(email="")
        membership = MembershipFactory(user=invited_user)

        send_org_invite(membership.pk, user.pk)

        send_mock.delay.assert_not_called()
