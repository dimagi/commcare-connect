from datetime import timedelta

import pytest
from django.utils.timezone import now

from commcare_connect.opportunity.models import CompletedWorkStatus, Opportunity
from commcare_connect.opportunity.tests.factories import (
    AssessmentFactory,
    CompletedWorkFactory,
    CredentialConfigurationFactory,
    OpportunityAccessFactory,
    UserCredentialFactory,
)
from commcare_connect.users.models import UserCredential
from commcare_connect.users.user_credentials import UserCredentialIssuer


@pytest.mark.django_db
class TestUserCredentialIssuer:
    def test_no_credentials_configured(self):
        CredentialConfigurationFactory()
        UserCredentialIssuer.run()

        assert not UserCredential.objects.exists()

    def test_issue_learning_credentials(self, opportunity):
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

    def test_issue_delivery_credentials(self, opportunity):
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
