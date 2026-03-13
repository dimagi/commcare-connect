import datetime
from unittest.mock import patch

import pytest

from commcare_connect.opportunity.models import CompletedWorkStatus, VisitValidationStatus
from commcare_connect.opportunity.tests.factories import (
    CompletedWorkFactory,
    OpportunityAccessFactory,
    UserVisitFactory,
)
from commcare_connect.program.models import ProgramApplicationStatus
from commcare_connect.program.tasks import (
    send_monthly_delivery_reminder_email,
    send_opportunity_created_email,
    send_opportunity_expiry_reminder_emails,
    send_program_invite_applied_email,
    send_program_invite_email,
)
from commcare_connect.program.tests.factories import (
    ManagedOpportunityFactory,
    ProgramApplicationFactory,
    ProgramFactory,
)
from commcare_connect.users.tests.factories import ProgramManagerOrgWithUsersFactory


@pytest.mark.django_db
@patch("commcare_connect.program.tasks.send_mail_async.delay")
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


@pytest.mark.django_db
@patch("commcare_connect.program.tasks.send_mail_async.delay")
def test_send_program_invited_notification(mock_send_mail):
    pm_org = ProgramManagerOrgWithUsersFactory()
    nm_org = ProgramManagerOrgWithUsersFactory()
    program_application = ProgramApplicationFactory(
        status=ProgramApplicationStatus.INVITED,
    )
    # Override the program's and application organization to be our PM org and NM org respectively
    program_application.program.organization = pm_org
    program_application.program.save()
    program_application.organization = nm_org
    program_application.save()

    send_program_invite_email(program_application.id)

    assert mock_send_mail.called
    call_kwargs = mock_send_mail.call_args[1]
    for membership in program_application.organization.memberships.all():
        assert membership.user.email in call_kwargs["recipient_list"]


@pytest.mark.django_db
@patch("commcare_connect.program.tasks.send_mail_async.delay")
def test_send_opportunity_created_notification(mock_send_mail):
    nm_org = ProgramManagerOrgWithUsersFactory()
    managed_opportunity = ManagedOpportunityFactory(
        organization=nm_org,
    )

    send_opportunity_created_email(managed_opportunity.id)

    assert mock_send_mail.called
    call_kwargs = mock_send_mail.call_args[1]
    for membership in nm_org.memberships.all():
        assert membership.user.email in call_kwargs["recipient_list"]


@pytest.mark.django_db
@patch("commcare_connect.program.tasks.send_mail_async")
class TestMonthlyDeliveryReminderEmail:
    def test_send_reminder_email_with_pending_deliveries(self, send_mock):
        org = ProgramManagerOrgWithUsersFactory()
        opportunity = ManagedOpportunityFactory(organization=org)

        access = OpportunityAccessFactory(opportunity=opportunity)
        completed_work = CompletedWorkFactory(opportunity_access=access, status=CompletedWorkStatus.pending)

        UserVisitFactory(
            opportunity=opportunity,
            user=access.user,
            opportunity_access=access,
            status=VisitValidationStatus.pending,
            completed_work=completed_work,
        )

        send_monthly_delivery_reminder_email()

        assert send_mock.delay.called
        call_args = send_mock.delay.call_args
        assert "Reminder: Please Review Pending Deliveries" in call_args.kwargs["subject"]
        assert org.get_member_emails() == call_args.kwargs["recipient_list"]

    def test_no_email_sent_without_pending_deliveries(self, send_mock):
        org = ProgramManagerOrgWithUsersFactory()
        opportunity = ManagedOpportunityFactory(organization=org)

        access = OpportunityAccessFactory(opportunity=opportunity)
        CompletedWorkFactory(opportunity_access=access, status=CompletedWorkStatus.approved)

        send_monthly_delivery_reminder_email()

        assert not send_mock.delay.called

    def test_no_email_sent_without_recipient_emails(self, send_mock):
        org = ProgramManagerOrgWithUsersFactory()

        org.memberships.all().delete()

        opportunity = ManagedOpportunityFactory(organization=org)
        access = OpportunityAccessFactory(opportunity=opportunity)
        completed_work = CompletedWorkFactory(opportunity_access=access, status=CompletedWorkStatus.pending)

        UserVisitFactory(
            opportunity=opportunity,
            user=access.user,
            opportunity_access=access,
            status=VisitValidationStatus.pending,
            completed_work=completed_work,
        )

        send_monthly_delivery_reminder_email()

        assert not send_mock.delay.called


@pytest.mark.django_db
@patch("commcare_connect.program.tasks.send_mail_async")
class TestOpportunityExpiryReminderEmails:
    def test_sends_reminder_7_days_before_end_date(self, mock_send_mail):
        pm_org = ProgramManagerOrgWithUsersFactory()
        program = ProgramFactory(organization=pm_org)
        ManagedOpportunityFactory(
            program=program,
            end_date=datetime.date.today() + datetime.timedelta(days=7),
            active=True,
        )

        send_opportunity_expiry_reminder_emails(7)

        assert mock_send_mail.delay.called
        call_kwargs = mock_send_mail.delay.call_args[1]
        assert "7" in call_kwargs["subject"]
        for email in pm_org.get_member_emails():
            assert email in call_kwargs["recipient_list"]

    def test_sends_reminder_3_days_before_end_date(self, mock_send_mail):
        pm_org = ProgramManagerOrgWithUsersFactory()
        program = ProgramFactory(organization=pm_org)
        ManagedOpportunityFactory(
            program=program,
            end_date=datetime.date.today() + datetime.timedelta(days=3),
            active=True,
        )

        send_opportunity_expiry_reminder_emails(3)

        assert mock_send_mail.delay.called
        call_kwargs = mock_send_mail.delay.call_args[1]
        assert "3" in call_kwargs["subject"]

    def test_no_email_sent_for_non_expiring_opportunity(self, mock_send_mail):
        pm_org = ProgramManagerOrgWithUsersFactory()
        program = ProgramFactory(organization=pm_org)
        ManagedOpportunityFactory(
            program=program,
            end_date=datetime.date.today() + datetime.timedelta(days=10),
            active=True,
        )

        send_opportunity_expiry_reminder_emails(7)

        assert not mock_send_mail.delay.called

    def test_no_email_sent_when_no_pm_members(self, mock_send_mail):
        pm_org = ProgramManagerOrgWithUsersFactory()
        pm_org.memberships.all().delete()
        program = ProgramFactory(organization=pm_org)
        ManagedOpportunityFactory(
            program=program,
            end_date=datetime.date.today() + datetime.timedelta(days=7),
            active=True,
        )

        send_opportunity_expiry_reminder_emails(7)

        assert not mock_send_mail.delay.called

    def test_groups_multiple_opportunities_per_pm_org(self, mock_send_mail):
        pm_org = ProgramManagerOrgWithUsersFactory()
        program = ProgramFactory(organization=pm_org)
        target_date = datetime.date.today() + datetime.timedelta(days=7)
        ManagedOpportunityFactory(program=program, end_date=target_date, active=True)
        ManagedOpportunityFactory(program=program, end_date=target_date, active=True)

        send_opportunity_expiry_reminder_emails(7)

        assert mock_send_mail.delay.call_count == 1
