from unittest.mock import patch

import pytest

from commcare_connect.program.models import ProgramApplicationStatus
from commcare_connect.program.tasks import send_program_invite_applied_email
from commcare_connect.program.tests.factories import ProgramApplicationFactory
from commcare_connect.users.tests.factories import ProgramManagerOrgWithUsersFactory


@pytest.mark.django_db
@patch("commcare_connect.program.tasks.send_mail")
def test_send_program_invite_applied_notification(mock_send_mail):
    pm_org = ProgramManagerOrgWithUsersFactory()
    program_application = ProgramApplicationFactory(
        status=ProgramApplicationStatus.APPLIED,
    )
    # Override the program's organization to be our PM org
    program_application.program.organization = pm_org
    program_application.program.save()

    send_program_invite_applied_email(program_application.id)

    assert mock_send_mail.called
    call_kwargs = mock_send_mail.call_args[1]
    for membership in pm_org.memberships.all():
        assert membership.user.email in call_kwargs["recipient_list"]
