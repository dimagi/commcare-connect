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
    get_org_managed_opps_for_review,
    get_org_opps_for_review,
    send_monthly_delivery_reminder_email,
)
from commcare_connect.program.tests.factories import ManagedOpportunityFactory, ProgramFactory
from commcare_connect.users.tests.factories import (
    OrganizationFactory,
    OrgWithUsersFactory,
    ProgramManagerOrgWithUsersFactory,
)


@pytest.mark.django_db
class TestGetOrgOppsIdsForReview:
    def test_org_no_opps_for_review(self):
        opportunity = OpportunityFactory(is_test=False)

        access = OpportunityAccessFactory(opportunity=opportunity)
        completed_work = CompletedWorkFactory(opportunity_access=access, status=CompletedWorkStatus.pending)

        UserVisitFactory(
            opportunity=opportunity,
            user=access.user,
            opportunity_access=access,
            status=VisitValidationStatus.approved,
            completed_work=completed_work,
        )
        opps = get_org_opps_for_review(opportunity.organization)
        assert len(opps) == 0

    def test_org_opps_for_review(self):
        opportunity = OpportunityFactory(is_test=False)

        access = OpportunityAccessFactory(opportunity=opportunity)
        completed_work = CompletedWorkFactory(opportunity_access=access, status=CompletedWorkStatus.pending)

        UserVisitFactory(
            opportunity=opportunity,
            user=access.user,
            opportunity_access=access,
            status=VisitValidationStatus.pending,
            completed_work=completed_work,
        )
        opps = get_org_opps_for_review(opportunity.organization)
        assert len(opps) == 1
        assert opps[0] == opportunity


@pytest.mark.django_db
class TestGetOrgManagedOppsIdsForReview:
    def test_org_no_managed_opps_for_review(self):
        pm_org = ProgramManagerOrgWithUsersFactory()
        nm_org = OrganizationFactory()

        program = ProgramFactory(organization=pm_org)
        managed_opportunity = ManagedOpportunityFactory(organization=nm_org, program=program, is_test=False)

        access = OpportunityAccessFactory(opportunity=managed_opportunity)
        completed_work = CompletedWorkFactory(opportunity_access=access, status=CompletedWorkStatus.pending)

        UserVisitFactory(
            opportunity=managed_opportunity,
            user=access.user,
            opportunity_access=access,
            status=VisitValidationStatus.pending,
            completed_work=completed_work,
        )
        opps = get_org_managed_opps_for_review(pm_org)
        assert len(opps) == 0

    def test_org_managed_opps_for_review(self):
        pm_org = ProgramManagerOrgWithUsersFactory()
        nm_org = OrganizationFactory()

        program = ProgramFactory(organization=pm_org)
        managed_opportunity = ManagedOpportunityFactory(organization=nm_org, program=program, is_test=False)

        access = OpportunityAccessFactory(opportunity=managed_opportunity)
        completed_work = CompletedWorkFactory(opportunity_access=access, status=CompletedWorkStatus.pending)

        UserVisitFactory(
            opportunity=managed_opportunity,
            user=access.user,
            opportunity_access=access,
            status=VisitValidationStatus.approved,
            review_status=VisitReviewStatus.pending,
            completed_work=completed_work,
        )
        opps = get_org_managed_opps_for_review(pm_org)
        assert len(opps) == 1
        assert opps[0].id == managed_opportunity.id


@pytest.mark.django_db
@patch("commcare_connect.program.tasks.send_mail_async")
class TestSendMonthlyDeliveryReminderEmail:
    def test_send_reminder_email_for_nm_org_with_no_pending_deliveries(self, send_mock):
        opportunity = OpportunityFactory(is_test=False)

        access = OpportunityAccessFactory(opportunity=opportunity)
        completed_work = CompletedWorkFactory(opportunity_access=access, status=CompletedWorkStatus.pending)

        UserVisitFactory(
            opportunity=opportunity,
            user=access.user,
            opportunity_access=access,
            status=VisitValidationStatus.rejected,
            completed_work=completed_work,
        )
        send_monthly_delivery_reminder_email()
        send_mock.delay.assert_not_called()

    def test_send_reminder_email_for_nm_org_with_pending_deliveries(self, send_mock):
        opportunity = OpportunityFactory(organization=OrgWithUsersFactory(), is_test=False)

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
        send_mock.delay.assert_called_once()

    def test_send_reminder_email_skipped_for_test_opportunity(self, send_mock):
        opportunity = OpportunityFactory(organization=OrgWithUsersFactory(), is_test=True)

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
        send_mock.delay.assert_not_called()

    def test_send_reminder_email_for_nm_org_with_no_recipients(self, send_mock):
        opportunity = OpportunityFactory(is_test=False)
        assert opportunity.organization.members.count() == 0

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
        send_mock.delay.assert_not_called()

    def test_send_reminder_email_for_pm_org_with_pending_managed_deliveries(self, send_mock):
        pm_org = ProgramManagerOrgWithUsersFactory()
        nm_org = OrganizationFactory()

        program = ProgramFactory(organization=pm_org)
        managed_opportunity = ManagedOpportunityFactory(organization=nm_org, program=program, is_test=False)

        access = OpportunityAccessFactory(opportunity=managed_opportunity)
        completed_work = CompletedWorkFactory(opportunity_access=access, status=CompletedWorkStatus.pending)

        UserVisitFactory(
            opportunity=managed_opportunity,
            user=access.user,
            opportunity_access=access,
            status=VisitValidationStatus.approved,
            review_status=VisitReviewStatus.pending,
            completed_work=completed_work,
        )
        send_monthly_delivery_reminder_email()
        send_mock.delay.assert_called_once()

    @patch("commcare_connect.program.tasks._send_org_email_for_opportunities")
    def test_send_reminder_email_for_multiple_orgs(self, send_mock, _):
        pm_org_1 = ProgramManagerOrgWithUsersFactory()
        pm_org_2 = ProgramManagerOrgWithUsersFactory()
        nm_org = OrgWithUsersFactory()

        program_1 = ProgramFactory(organization=pm_org_1)
        program_2 = ProgramFactory(organization=pm_org_2)

        managed_opportunity_1 = ManagedOpportunityFactory(organization=nm_org, program=program_1, is_test=False)
        managed_opportunity_2 = ManagedOpportunityFactory(organization=nm_org, program=program_2, is_test=False)
        nm_opportunity = OpportunityFactory(organization=nm_org, is_test=False)
        pm_opportunity = OpportunityFactory(organization=pm_org_2, is_test=False)

        access_1 = OpportunityAccessFactory(opportunity=managed_opportunity_1)
        access_2 = OpportunityAccessFactory(opportunity=managed_opportunity_2)
        access_3 = OpportunityAccessFactory(opportunity=nm_opportunity)
        access_4 = OpportunityAccessFactory(opportunity=pm_opportunity)

        completed_work_1 = CompletedWorkFactory(opportunity_access=access_1, status=CompletedWorkStatus.pending)
        completed_work_2 = CompletedWorkFactory(opportunity_access=access_2, status=CompletedWorkStatus.pending)
        completed_work_3 = CompletedWorkFactory(opportunity_access=access_3, status=CompletedWorkStatus.pending)
        completed_work_4 = CompletedWorkFactory(opportunity_access=access_4, status=CompletedWorkStatus.pending)

        UserVisitFactory(
            opportunity=managed_opportunity_1,
            user=access_1.user,
            opportunity_access=access_1,
            status=VisitValidationStatus.pending,
            review_status=VisitReviewStatus.pending,
            completed_work=completed_work_1,
        )
        UserVisitFactory(
            opportunity=managed_opportunity_2,
            user=access_2.user,
            opportunity_access=access_2,
            status=VisitValidationStatus.approved,
            review_status=VisitReviewStatus.pending,
            completed_work=completed_work_2,
        )
        # Opportunity visit on NM
        UserVisitFactory(
            opportunity=nm_opportunity,
            user=access_3.user,
            opportunity_access=access_3,
            status=VisitValidationStatus.pending,
            completed_work=completed_work_3,
        )
        # Opportunity visit on PM 2
        UserVisitFactory(
            opportunity=pm_opportunity,
            user=access_4.user,
            opportunity_access=access_4,
            status=VisitValidationStatus.pending,
            completed_work=completed_work_4,
        )
        send_monthly_delivery_reminder_email()

        assert send_mock.call_count == 2

        pm_org_2_member_emails = list(pm_org_2.members.values_list("email", flat=True))
        nm_org_member_emails = list(nm_org.members.values_list("email", flat=True))

        args_list = send_mock.call_args_list
        call_1_args = args_list[0][1]
        call_2_args = args_list[1][1]

        pm_call_args = args_list[0][1] if call_1_args["organization"] == pm_org_2 else args_list[1][1]
        nm_call_args = args_list[1][1] if call_2_args["organization"] == nm_org else args_list[0][1]

        assert pm_call_args["organization"] == pm_org_2
        assert pm_call_args["recipient_emails"] == pm_org_2_member_emails
        assert len(pm_call_args["opportunities"]) == 2
        expected_opps = {pm_opportunity.id, managed_opportunity_2.id}
        actual_opps = {pm_call_args["opportunities"][0].id, pm_call_args["opportunities"][1].id}
        assert expected_opps == actual_opps

        assert nm_call_args["organization"] == nm_org
        assert nm_call_args["recipient_emails"] == nm_org_member_emails
        assert len(nm_call_args["opportunities"]) == 2
        expected_opps = {nm_opportunity.id, managed_opportunity_1.id}
        actual_opps = {nm_call_args["opportunities"][0].id, nm_call_args["opportunities"][1].id}
        assert expected_opps == actual_opps
