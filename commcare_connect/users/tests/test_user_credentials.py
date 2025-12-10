from datetime import timedelta
from unittest import mock

import pytest
from django.db.models import F
from django.utils.timezone import now

from commcare_connect.opportunity.models import CompletedWorkStatus, Opportunity
from commcare_connect.opportunity.tests.factories import (
    AssessmentFactory,
    CompletedWorkFactory,
    CredentialConfigurationFactory,
    OpportunityAccessFactory,
    UserCredentialFactory,
)
from commcare_connect.users.models import User, UserCredential
from commcare_connect.users.user_credentials import UserCredentialIssuer


@pytest.mark.django_db
class TestUserCredentialIssuer:
    def test_no_credentials_configured(self):
        CredentialConfigurationFactory()
        UserCredentialIssuer.run()

        assert not UserCredential.objects.exists()

    @mock.patch("commcare_connect.opportunity.tasks.submit_credentials_to_personalid_task")
    def test_issue_learning_credentials(self, mock_submit_credentials_to_personalid_task, opportunity):
        CredentialConfigurationFactory(
            opportunity=opportunity,
            learn_level=UserCredential.LearnLevel.LEARN_PASSED,
        )

        access1 = OpportunityAccessFactory(opportunity=opportunity, accepted=True)
        access1.completed_learn_date = now() - timedelta(days=1)
        access1.save()

        access2 = OpportunityAccessFactory(opportunity=opportunity, accepted=True)
        access2.completed_learn_date = now() - timedelta(days=2)
        access2.save()

        AssessmentFactory(opportunity_access=access1, opportunity=opportunity, passed=True)
        AssessmentFactory(opportunity_access=access2, opportunity=opportunity, passed=True)

        UserCredentialIssuer.run()

        expected_users_earning_credentials = {access1.user.id, access2.user.id}
        assert (
            set(UserCredential.objects.all().values_list("user_id", flat=True)) == expected_users_earning_credentials
        )

    @mock.patch("commcare_connect.opportunity.tasks.submit_credentials_to_personalid_task")
    def test_issue_delivery_credentials(self, mock_submit_credentials_to_personalid_task, opportunity):
        access1 = OpportunityAccessFactory(opportunity=opportunity, accepted=True)
        access2 = OpportunityAccessFactory(opportunity=opportunity, accepted=True)

        CompletedWorkFactory.create_batch(1, opportunity_access=access1, status=CompletedWorkStatus.approved)
        CompletedWorkFactory.create_batch(50, opportunity_access=access2, status=CompletedWorkStatus.approved)

        CredentialConfigurationFactory(
            opportunity=opportunity,
            delivery_level=UserCredential.DeliveryLevel.FIFTY,
        )
        UserCredentialIssuer.run()

        expected_users_earning_credentials = {access2.user.id}
        assert (
            set(UserCredential.objects.all().values_list("user_id", flat=True)) == expected_users_earning_credentials
        )

    def test_learning_creds_no_credentials_issued_yet(self, opportunity):
        CredentialConfigurationFactory(
            opportunity=opportunity,
            learn_level=UserCredential.LearnLevel.LEARN_PASSED,
        )

        access = OpportunityAccessFactory(opportunity=opportunity, accepted=True)
        access.completed_learn_date = now() - timedelta(days=1)
        access.save()

        AssessmentFactory(opportunity_access=access, opportunity=opportunity, passed=True)

        users_earning_creds = UserCredentialIssuer.get_learning_user_credentials(
            Opportunity.objects.filter(id=opportunity.id), UserCredential.LearnLevel.LEARN_PASSED
        )
        assert len(users_earning_creds) == 1

        cred_to_be_issued = users_earning_creds[0]
        assert cred_to_be_issued.user_id == access.user.id
        assert cred_to_be_issued.opportunity == opportunity
        assert cred_to_be_issued.level == UserCredential.LearnLevel.LEARN_PASSED
        assert cred_to_be_issued.credential_type == UserCredential.CredentialType.LEARN
        assert cred_to_be_issued.delivery_type == opportunity.delivery_type

    def test_learning_creds_existing_credential_users_skipped(self, opportunity):
        CredentialConfigurationFactory(
            opportunity=opportunity,
            learn_level=UserCredential.LearnLevel.LEARN_PASSED,
        )

        access1 = OpportunityAccessFactory(opportunity=opportunity, accepted=True)
        access1.completed_learn_date = now() - timedelta(days=1)
        access1.save()

        AssessmentFactory(opportunity_access=access1, opportunity=opportunity, passed=True)

        UserCredentialFactory(
            user=access1.user,
            opportunity=opportunity,
            credential_type=UserCredential.CredentialType.LEARN,
            level=UserCredential.LearnLevel.LEARN_PASSED,
        )
        assert UserCredential.objects.count() == 1

        opp_query = Opportunity.objects.filter(id=opportunity.id)
        users_earning_creds = UserCredentialIssuer.get_learning_user_credentials(
            opp_query, UserCredential.LearnLevel.LEARN_PASSED
        )
        assert len(users_earning_creds) == 0

        access2 = OpportunityAccessFactory(opportunity=opportunity, accepted=True)
        access2.completed_learn_date = now() - timedelta(days=1)
        access2.save()

        AssessmentFactory(opportunity_access=access2, opportunity=opportunity, passed=True)

        access3 = OpportunityAccessFactory(opportunity=opportunity, accepted=True)
        access3.completed_learn_date = now() - timedelta(days=1)
        access3.save()

        AssessmentFactory(opportunity_access=access3, opportunity=opportunity, passed=False)

        users_earning_creds = UserCredentialIssuer.get_learning_user_credentials(
            opp_query, UserCredential.LearnLevel.LEARN_PASSED
        )
        assert len(users_earning_creds) == 1
        assert users_earning_creds[0].user_id == access2.user.id

    def test_delivery_creds_no_credentials_issued_yet(self, opportunity):
        access1 = OpportunityAccessFactory(opportunity=opportunity, accepted=True)
        access2 = OpportunityAccessFactory(opportunity=opportunity, accepted=True)

        CompletedWorkFactory.create_batch(50, opportunity_access=access1, status=CompletedWorkStatus.approved)
        CompletedWorkFactory.create_batch(5, opportunity_access=access2, status=CompletedWorkStatus.approved)

        assert UserCredential.objects.count() == 0

        cred_config = CredentialConfigurationFactory(
            opportunity=opportunity,
            delivery_level=UserCredential.DeliveryLevel.FIFTY,
        )
        # Refresh from db to convert enum to string value
        cred_config.refresh_from_db()

        users_earning_creds = UserCredentialIssuer.get_delivery_user_credentials(
            Opportunity.objects.filter(id=opportunity.id), UserCredential.DeliveryLevel.FIFTY
        )
        assert len(users_earning_creds) == 1

        cred_to_be_issued = users_earning_creds[0]
        assert cred_to_be_issued.user_id == access1.user.id
        assert cred_to_be_issued.opportunity == opportunity
        assert cred_to_be_issued.level == UserCredential.DeliveryLevel.FIFTY
        assert cred_to_be_issued.credential_type == UserCredential.CredentialType.DELIVERY
        assert cred_to_be_issued.delivery_type == opportunity.delivery_type

    def test_delivery_creds_existing_credential_users_skipped(self, opportunity):
        cred_config = CredentialConfigurationFactory(
            opportunity=opportunity,
            delivery_level=UserCredential.DeliveryLevel.FIFTY,
        )
        cred_config.refresh_from_db()

        access1 = OpportunityAccessFactory(opportunity=opportunity, accepted=True)
        CompletedWorkFactory.create_batch(50, opportunity_access=access1, status=CompletedWorkStatus.approved)

        UserCredentialFactory(
            user=access1.user,
            opportunity=opportunity,
            credential_type=UserCredential.CredentialType.DELIVERY,
            level=UserCredential.DeliveryLevel.FIFTY,
        )
        assert UserCredential.objects.count() == 1

        opp_query = Opportunity.objects.filter(id=opportunity.id)
        users_earning_creds = UserCredentialIssuer.get_delivery_user_credentials(
            opp_query, UserCredential.DeliveryLevel.FIFTY
        )
        assert len(users_earning_creds) == 0

        access2 = OpportunityAccessFactory(opportunity=opportunity, accepted=True)
        CompletedWorkFactory.create_batch(50, opportunity_access=access2, status=CompletedWorkStatus.approved)

        users_earning_creds = UserCredentialIssuer.get_delivery_user_credentials(
            opp_query, UserCredential.DeliveryLevel.FIFTY
        )
        assert len(users_earning_creds) == 1
        assert users_earning_creds[0].user_id == access2.user.id

    @mock.patch.object(UserCredentialIssuer, "_submit_credentials_to_personal_id")
    def test_no_credentials_to_submit(self, mock_submit_credentials_to_personal_id):
        UserCredentialFactory(issued_on=now())
        UserCredentialIssuer.submit_user_credentials()
        mock_submit_credentials_to_personal_id.assert_not_called()

    @mock.patch.object(UserCredentialIssuer, "_submit_credentials_to_personal_id")
    def test_credentials_creates_valid_payloads(self, mock_submit_credentials_to_personal_id, opportunity):
        # Three users earning the same delivery credentials for the same opportunity
        opportunity_delivery_user_creds = UserCredentialFactory.create_batch(
            3,
            issued_on=None,
            credential_type=UserCredential.CredentialType.DELIVERY,
            level=UserCredential.DeliveryLevel.FIFTY,
            opportunity=opportunity,
            delivery_type=opportunity.delivery_type,
        )

        # Three users earning learning credentials for different opportunities
        UserCredentialFactory.create_batch(
            3,
            issued_on=None,
            level=UserCredential.LearnLevel.LEARN_PASSED,
            credential_type=UserCredential.CredentialType.LEARN,
        )

        # Make sure usernames are populated
        User.objects.all().update(username=F("email"))

        UserCredentialIssuer.submit_user_credentials()

        assert mock_submit_credentials_to_personal_id.called
        assert mock_submit_credentials_to_personal_id.call_count == 1

        args_, _ = mock_submit_credentials_to_personal_id.call_args
        credential_id_sets_index_arg = args_[0]
        credentials_payload_items_arg = args_[1]

        # Find the delivery credentials payload item and it's index in the list
        (list_index, opp_delivery_payload_item) = next(
            (
                (index, item)
                for index, item in enumerate(credentials_payload_items_arg)
                if item["type"] == UserCredential.CredentialType.DELIVERY
            )
        )

        # Check that the credential IDs are correctly mapped
        expected_credential_ids = {cred.id for cred in opportunity_delivery_user_creds}
        actual_credential_ids = set(credential_id_sets_index_arg[list_index])
        assert expected_credential_ids == actual_credential_ids

        # Check that all usernames earning the same credential are included in the same credential payload item
        expected_usernames_in_payload_item = UserCredential.objects.filter(id__in=expected_credential_ids).values_list(
            "user__username", flat=True
        )

        assert set(opp_delivery_payload_item["usernames"]) == set(expected_usernames_in_payload_item)
        assert opp_delivery_payload_item["level"] == UserCredential.DeliveryLevel.FIFTY
        assert opp_delivery_payload_item["opportunity_id"] == opportunity.id

    @mock.patch("commcare_connect.users.user_credentials.add_credentials_on_personalid")
    def test_credentials_submitted_and_issued(self, mock_add_credentials_on_personalid, opportunity):
        UserCredentialFactory.create_batch(
            3,
            issued_on=None,
            credential_type=UserCredential.CredentialType.DELIVERY,
            level=UserCredential.DeliveryLevel.FIFTY,
            opportunity=opportunity,
            delivery_type=opportunity.delivery_type,
        )
        # Each learning credential is for a different opportunity
        UserCredentialFactory.create_batch(
            3,
            issued_on=None,
            level=UserCredential.LearnLevel.LEARN_PASSED,
            credential_type=UserCredential.CredentialType.LEARN,
        )
        assert UserCredential.objects.filter(issued_on__isnull=True).count() == 6

        # All credentials will be "successfully" submitted, hence return all payload indices
        mock_add_credentials_on_personalid.return_value = {"success": [0, 1, 2, 3], "failed": []}
        UserCredentialIssuer.submit_user_credentials()

        assert UserCredential.objects.filter(issued_on__isnull=False).count() == 6

    @mock.patch("commcare_connect.users.user_credentials.add_credentials_on_personalid")
    @mock.patch.object(UserCredentialIssuer, "USERNAME_CHUNK_SIZE", 2)
    def test_payload_username_chunking(self, mock_add_credentials_on_personalid, opportunity):
        UserCredentialFactory.create_batch(
            4,
            issued_on=None,
            credential_type=UserCredential.CredentialType.DELIVERY,
            level=UserCredential.DeliveryLevel.FIFTY,
            opportunity=opportunity,
            delivery_type=opportunity.delivery_type,
        )
        assert UserCredential.objects.filter(issued_on__isnull=True).count() == 4

        # Make sure usernames are populated
        for user in UserCredential.objects.all().values_list("user", flat=True):
            User.objects.filter(id=user).update(username=f"user_{user}")

        # All credentials will be "successfully" submitted, hence return all payload indices
        # In this case, there should be 2 payloads created due to chunking (4 usernames, chunk size 2)
        mock_add_credentials_on_personalid.return_value = {"success": [0, 1], "failed": []}
        UserCredentialIssuer.submit_user_credentials()

        assert UserCredential.objects.filter(issued_on__isnull=False).count() == 4
