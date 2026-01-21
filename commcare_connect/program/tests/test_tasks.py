from unittest.mock import patch

import pytest

from commcare_connect.opportunity.models import CompletedWorkStatus, VisitReviewStatus, VisitValidationStatus
from commcare_connect.opportunity.tests.factories import (
    CompletedWorkFactory,
    OpportunityAccessFactory,
    OpportunityFactory,
    UserVisitFactory,
)
from commcare_connect.program.tasks import (
    send_monthly_delivery_reminder_email,
    send_nm_reminder_for_opportunities,
    send_pm_reminder_for_opportunities,
)
from commcare_connect.program.tests.factories import ManagedOpportunityFactory, ProgramFactory
from commcare_connect.users.tests.factories import OrganizationFactory, ProgramManagerOrgWithUsersFactory


@pytest.mark.django_db
@patch("commcare_connect.program.tasks.send_mail_async")
class TestSendMonthlyDeliveryReminderEmail:
    def test_send_reminder_email_with_pending_deliveries(self, send_mock):
        org = ProgramManagerOrgWithUsersFactory()
        program = ProgramFactory(organization=org)
        opportunity = ManagedOpportunityFactory(organization=org, program=program)

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
        program = ProgramFactory(organization=org)
        opportunity = ManagedOpportunityFactory(organization=org, program=program)

        access = OpportunityAccessFactory(opportunity=opportunity)
        CompletedWorkFactory(opportunity_access=access, status=CompletedWorkStatus.approved)

        send_monthly_delivery_reminder_email()

        assert not send_mock.delay.called

    def test_no_email_sent_without_recipient_emails(self, send_mock):
        org = ProgramManagerOrgWithUsersFactory()
        org.memberships.all().delete()

        program = ProgramFactory(organization=org)
        opportunity = ManagedOpportunityFactory(organization=org, program=program)
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

    def test_send_reminder_email_for_multiple_organizations(self, send_mock):
        org1 = ProgramManagerOrgWithUsersFactory()
        org2 = ProgramManagerOrgWithUsersFactory()

        program1 = ProgramFactory(organization=org1)
        program2 = ProgramFactory(organization=org2)
        opportunity1 = ManagedOpportunityFactory(organization=org1, program=program1)
        opportunity2 = ManagedOpportunityFactory(organization=org2, program=program2)

        access1 = OpportunityAccessFactory(opportunity=opportunity1)
        access2 = OpportunityAccessFactory(opportunity=opportunity2)

        completed_work1 = CompletedWorkFactory(opportunity_access=access1, status=CompletedWorkStatus.pending)
        completed_work2 = CompletedWorkFactory(opportunity_access=access2, status=CompletedWorkStatus.pending)

        UserVisitFactory(
            opportunity=opportunity1,
            user=access1.user,
            opportunity_access=access1,
            status=VisitValidationStatus.pending,
            completed_work=completed_work1,
        )
        UserVisitFactory(
            opportunity=opportunity2,
            user=access2.user,
            opportunity_access=access2,
            status=VisitValidationStatus.pending,
            completed_work=completed_work2,
        )

        send_monthly_delivery_reminder_email()

        assert send_mock.delay.call_count == 2


@pytest.mark.django_db
@patch("commcare_connect.program.tasks._send_org_email_for_opportunities")
class TestSendNmReminderForOpportunities:
    def test_send_nm_reminder_with_pending_visits(self, send_email_mock):
        nm_org = OrganizationFactory()
        program = ProgramFactory()
        opportunity = ManagedOpportunityFactory(organization=nm_org, program=program)

        access = OpportunityAccessFactory(opportunity=opportunity)
        completed_work = CompletedWorkFactory(opportunity_access=access, status=CompletedWorkStatus.pending)

        UserVisitFactory(
            opportunity=opportunity,
            user=access.user,
            opportunity_access=access,
            status=VisitValidationStatus.pending,
            completed_work=completed_work,
        )

        send_nm_reminder_for_opportunities(nm_org)

        assert send_email_mock.called
        call_args = send_email_mock.call_args[1]
        assert call_args["organization"] == nm_org
        assert len(call_args["opportunities"]) == 1
        assert call_args["opportunities"][0].id == opportunity.id

    def test_nm_reminder_not_sent_for_approved_visits(self, send_email_mock):
        nm_org = OrganizationFactory()
        program = ProgramFactory()
        opportunity = ManagedOpportunityFactory(organization=nm_org, program=program)

        access = OpportunityAccessFactory(opportunity=opportunity)
        completed_work = CompletedWorkFactory(opportunity_access=access, status=CompletedWorkStatus.pending)

        UserVisitFactory(
            opportunity=opportunity,
            user=access.user,
            opportunity_access=access,
            status=VisitValidationStatus.approved,
            completed_work=completed_work,
        )

        send_nm_reminder_for_opportunities(nm_org)

        assert not send_email_mock.called

    def test_nm_reminder_with_multiple_opportunities(self, send_email_mock):
        nm_org = OrganizationFactory()
        program1 = ProgramFactory()
        program2 = ProgramFactory()
        opportunity1 = ManagedOpportunityFactory(organization=nm_org, program=program1)
        opportunity2 = ManagedOpportunityFactory(organization=nm_org, program=program2)

        access1 = OpportunityAccessFactory(opportunity=opportunity1)
        access2 = OpportunityAccessFactory(opportunity=opportunity2)

        completed_work1 = CompletedWorkFactory(opportunity_access=access1, status=CompletedWorkStatus.pending)
        completed_work2 = CompletedWorkFactory(opportunity_access=access2, status=CompletedWorkStatus.pending)

        UserVisitFactory(
            opportunity=opportunity1,
            user=access1.user,
            opportunity_access=access1,
            status=VisitValidationStatus.pending,
            completed_work=completed_work1,
        )
        UserVisitFactory(
            opportunity=opportunity2,
            user=access2.user,
            opportunity_access=access2,
            status=VisitValidationStatus.pending,
            completed_work=completed_work2,
        )

        send_nm_reminder_for_opportunities(nm_org)

        assert send_email_mock.called
        call_args = send_email_mock.call_args[1]
        assert len(call_args["opportunities"]) == 2
        opportunity_ids = {opp.id for opp in call_args["opportunities"]}
        assert opportunity_ids == {opportunity1.id, opportunity2.id}


@pytest.mark.django_db
@patch("commcare_connect.program.tasks._send_org_email_for_opportunities")
class TestSendPmReminderForOpportunities:
    def test_send_pm_reminder_with_pending_review(self, send_email_mock):
        nm_org = OrganizationFactory()
        pm_org = ProgramManagerOrgWithUsersFactory()
        program = ProgramFactory(organization=pm_org)
        opportunity = ManagedOpportunityFactory(organization=nm_org, program=program)

        access = OpportunityAccessFactory(opportunity=opportunity)
        completed_work = CompletedWorkFactory(opportunity_access=access, status=CompletedWorkStatus.pending)

        UserVisitFactory(
            opportunity=opportunity,
            user=access.user,
            opportunity_access=access,
            status=VisitValidationStatus.approved,
            review_status=VisitReviewStatus.pending,
            completed_work=completed_work,
        )

        send_pm_reminder_for_opportunities(nm_org)

        assert send_email_mock.called
        call_args = send_email_mock.call_args[1]
        assert call_args["organization"] == nm_org
        assert len(call_args["opportunities"]) == 1
        assert call_args["opportunities"][0].id == opportunity.id
        # Should include PM organization member emails
        expected_emails = set(pm_org.get_member_emails())
        actual_emails = set(call_args["recipient_emails"])
        assert actual_emails == expected_emails

    def test_pm_reminder_not_sent_for_non_approved_visits(self, send_email_mock):
        nm_org = OrganizationFactory()
        pm_org = ProgramManagerOrgWithUsersFactory()
        program = ProgramFactory(organization=pm_org)
        opportunity = ManagedOpportunityFactory(organization=nm_org, program=program)

        access = OpportunityAccessFactory(opportunity=opportunity)
        completed_work = CompletedWorkFactory(opportunity_access=access, status=CompletedWorkStatus.pending)

        UserVisitFactory(
            opportunity=opportunity,
            user=access.user,
            opportunity_access=access,
            status=VisitValidationStatus.pending,
            review_status=VisitReviewStatus.pending,
            completed_work=completed_work,
        )

        send_pm_reminder_for_opportunities(nm_org)

        assert not send_email_mock.called

    def test_pm_reminder_not_sent_for_completed_review(self, send_email_mock):
        nm_org = OrganizationFactory()
        pm_org = ProgramManagerOrgWithUsersFactory()
        program = ProgramFactory(organization=pm_org)
        opportunity = ManagedOpportunityFactory(organization=nm_org, program=program)

        access = OpportunityAccessFactory(opportunity=opportunity)
        completed_work = CompletedWorkFactory(opportunity_access=access, status=CompletedWorkStatus.pending)

        UserVisitFactory(
            opportunity=opportunity,
            user=access.user,
            opportunity_access=access,
            status=VisitValidationStatus.approved,
            review_status=VisitReviewStatus.agree,
            completed_work=completed_work,
        )

        send_pm_reminder_for_opportunities(nm_org)

        assert not send_email_mock.called

    def test_pm_reminder_not_sent_for_unmanaged_opportunity(self, send_email_mock):
        nm_org = OrganizationFactory()

        opportunity = OpportunityFactory(organization=nm_org)

        access = OpportunityAccessFactory(opportunity=opportunity)
        completed_work = CompletedWorkFactory(opportunity_access=access, status=CompletedWorkStatus.pending)

        UserVisitFactory(
            opportunity=opportunity,
            user=access.user,
            opportunity_access=access,
            status=VisitValidationStatus.approved,
            review_status=VisitReviewStatus.pending,
            completed_work=completed_work,
        )

        send_pm_reminder_for_opportunities(nm_org)

        assert not send_email_mock.called

    def test_pm_reminder_with_multiple_pm_organizations(self, send_email_mock):
        nm_org = OrganizationFactory()
        pm_org1 = ProgramManagerOrgWithUsersFactory()
        pm_org2 = ProgramManagerOrgWithUsersFactory()

        program1 = ProgramFactory(organization=pm_org1)
        program2 = ProgramFactory(organization=pm_org2)
        opportunity1 = ManagedOpportunityFactory(organization=nm_org, program=program1)
        opportunity2 = ManagedOpportunityFactory(organization=nm_org, program=program2)

        access1 = OpportunityAccessFactory(opportunity=opportunity1)
        access2 = OpportunityAccessFactory(opportunity=opportunity2)

        completed_work1 = CompletedWorkFactory(opportunity_access=access1, status=CompletedWorkStatus.pending)
        completed_work2 = CompletedWorkFactory(opportunity_access=access2, status=CompletedWorkStatus.pending)

        UserVisitFactory(
            opportunity=opportunity1,
            user=access1.user,
            opportunity_access=access1,
            status=VisitValidationStatus.approved,
            review_status=VisitReviewStatus.pending,
            completed_work=completed_work1,
        )
        UserVisitFactory(
            opportunity=opportunity2,
            user=access2.user,
            opportunity_access=access2,
            status=VisitValidationStatus.approved,
            review_status=VisitReviewStatus.pending,
            completed_work=completed_work2,
        )

        send_pm_reminder_for_opportunities(nm_org)

        assert send_email_mock.called
        call_args = send_email_mock.call_args[1]
        assert len(call_args["opportunities"]) == 2

        # Should include emails from both PM organizations
        expected_emails = set(pm_org1.get_member_emails() + pm_org2.get_member_emails())
        actual_emails = set(call_args["recipient_emails"])
        assert actual_emails == expected_emails

    def test_pm_reminder_not_sent_without_pending_review(self, send_email_mock):
        nm_org = OrganizationFactory()
        program = ProgramFactory()
        opportunity = ManagedOpportunityFactory(organization=nm_org, program=program)

        access = OpportunityAccessFactory(opportunity=opportunity)
        CompletedWorkFactory(opportunity_access=access, status=CompletedWorkStatus.approved)

        send_pm_reminder_for_opportunities(nm_org)

        assert not send_email_mock.called
