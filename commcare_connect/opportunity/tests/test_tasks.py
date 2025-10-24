import datetime
from datetime import timedelta
from unittest import mock

import pytest
from django.utils.timezone import now

from commcare_connect.connect_id_client.models import ConnectIdUser
from commcare_connect.opportunity.models import BlobMeta, CompletedWorkStatus, Opportunity, OpportunityAccess
from commcare_connect.opportunity.tasks import (
    _get_inactive_message,
    add_connect_users,
    download_user_visit_attachments,
    get_delivery_user_credentials,
    get_learning_user_credentials,
    issue_user_credentials,
)
from commcare_connect.opportunity.tests.factories import (
    AssessmentFactory,
    CompletedModuleFactory,
    CompletedWorkFactory,
    CredentialConfigurationFactory,
    LearnModuleFactory,
    OpportunityAccessFactory,
    OpportunityClaimFactory,
    OpportunityFactory,
    UserCredentialFactory,
    UserVisitFactory,
)
from commcare_connect.users.credential_levels import DeliveryLevel, LearnLevel
from commcare_connect.users.models import User, UserCredential


class TestConnectUserCreation:
    @pytest.mark.django_db
    def test_add_connect_user(self, httpx_mock):
        opportunity = OpportunityFactory()
        with (
            mock.patch("commcare_connect.opportunity.tasks.fetch_users") as fetch_users,
            mock.patch("commcare_connect.opportunity.tasks.invite_user"),
        ):
            fetch_users.return_value = [
                ConnectIdUser(username="test", phone_number="+15555555555", name="a"),
                ConnectIdUser(username="test2", phone_number="+12222222222", name="b"),
            ]
            add_connect_users(["+15555555555", "+12222222222"], opportunity.id)

        user_list = User.objects.filter(username="test")
        assert len(user_list) == 1
        user = user_list[0]
        assert user.name == "a"
        assert user.phone_number == "+15555555555"
        assert len(OpportunityAccess.objects.filter(user=user_list.first(), opportunity=opportunity)) == 1

        user2 = User.objects.filter(username="test2")
        assert len(user2) == 1
        assert len(OpportunityAccess.objects.filter(user=user2.first(), opportunity=opportunity)) == 1


def test_send_inactive_notification_learn_inactive_message(mobile_user: User, opportunity: Opportunity):
    learn_modules = LearnModuleFactory.create_batch(2, app=opportunity.learn_app)
    access = OpportunityAccess.objects.get(user=mobile_user, opportunity=opportunity)
    CompletedModuleFactory.create(
        date=now() - datetime.timedelta(days=3),
        user=mobile_user,
        opportunity=opportunity,
        module=learn_modules[0],
        opportunity_access=access,
    )
    access.refresh_from_db()
    message = _get_inactive_message(access)
    assert message is not None
    assert message.usernames[0] == mobile_user.username
    assert message.title == f"Resume your learning journey for {opportunity.name}"


def test_send_inactive_notification_deliver_inactive_message(mobile_user: User, opportunity: Opportunity):
    learn_modules = LearnModuleFactory.create_batch(2, app=opportunity.learn_app)
    access = OpportunityAccess.objects.get(user=mobile_user, opportunity=opportunity)
    for learn_module in learn_modules:
        CompletedModuleFactory.create(
            user=mobile_user,
            opportunity=opportunity,
            module=learn_module,
            date=now() - datetime.timedelta(days=2),
            opportunity_access=access,
        )
    access.refresh_from_db()
    OpportunityClaimFactory.create(opportunity_access=access, end_date=opportunity.end_date)
    UserVisitFactory.create(
        user=mobile_user,
        opportunity=opportunity,
        visit_date=now() - datetime.timedelta(days=2),
        opportunity_access=access,
    )

    message = _get_inactive_message(access)
    assert message is not None
    assert message.usernames[0] == mobile_user.username
    assert message.title == f"Resume your job for {opportunity.name}"


def test_send_inactive_notification_not_claimed_deliver_message(mobile_user: User, opportunity: Opportunity):
    learn_modules = LearnModuleFactory.create_batch(2, app=opportunity.learn_app)
    access = OpportunityAccess.objects.get(user=mobile_user, opportunity=opportunity)
    for learn_module in learn_modules:
        CompletedModuleFactory.create(
            user=mobile_user,
            opportunity=opportunity,
            module=learn_module,
            date=now() - datetime.timedelta(days=2),
            opportunity_access=access,
        )
    message = _get_inactive_message(access)
    assert message is not None
    assert message.usernames[0] == mobile_user.username
    assert message.title == f"Resume your job for {opportunity.name}"


def test_send_inactive_notification_active_user(mobile_user: User, opportunity: Opportunity):
    learn_modules = LearnModuleFactory.create_batch(2, app=opportunity.learn_app)
    access = OpportunityAccess.objects.get(user=mobile_user, opportunity=opportunity)
    for learn_module in learn_modules:
        CompletedModuleFactory.create(
            user=mobile_user,
            opportunity=opportunity,
            module=learn_module,
            date=now() - datetime.timedelta(days=2),
            opportunity_access=access,
        )
    access = OpportunityAccess.objects.get(user=mobile_user, opportunity=opportunity)
    OpportunityClaimFactory.create(opportunity_access=access, end_date=opportunity.end_date)
    UserVisitFactory.create(
        user=mobile_user,
        opportunity=opportunity,
        visit_date=now() - datetime.timedelta(days=1),
        opportunity_access=access,
    )
    message = _get_inactive_message(access)
    assert message is None


def test_download_attachments(mobile_user: User, opportunity: Opportunity):
    learn_modules = LearnModuleFactory.create_batch(2, app=opportunity.learn_app)
    for learn_module in learn_modules:
        CompletedModuleFactory.create(
            user=mobile_user,
            opportunity=opportunity,
            module=learn_module,
            date=now() - datetime.timedelta(days=2),
        )
    access = OpportunityAccess.objects.get(user=mobile_user, opportunity=opportunity)
    OpportunityClaimFactory.create(opportunity_access=access, end_date=opportunity.end_date)
    user_visit = UserVisitFactory.create(
        user=mobile_user,
        opportunity=opportunity,
        form_json={"attachments": {"myimage.jpg": {"content_type": "image/jpeg", "length": 20}}},
    )
    with mock.patch("commcare_connect.opportunity.tasks.httpx.get") as get_response, mock.patch(
        "commcare_connect.opportunity.tasks.default_storage.save"
    ) as save_blob:
        get_response.return_value.content = b"asdas"
        download_user_visit_attachments(user_visit.id)
        blob_meta = BlobMeta.objects.first()

        assert blob_meta.name == "myimage.jpg"
        assert blob_meta.parent_id == user_visit.xform_id
        assert blob_meta.content_length == 20
        assert blob_meta.content_type == "image/jpeg"
        blob_id, content_file = save_blob.call_args_list[0].args
        assert str(blob_id) == blob_meta.blob_id
        assert content_file.read() == b"asdas"


@pytest.mark.django_db
class TestIssueCredentialsTask:
    def test_no_credentials_configured(self):
        CredentialConfigurationFactory()
        issue_user_credentials()

        assert not UserCredential.objects.exists()

    def test_issue_learning_credentials(self, opportunity):
        CredentialConfigurationFactory(
            opportunity=opportunity,
            learn_level=LearnLevel.LEARN_PASSED,
        )

        access1 = OpportunityAccessFactory(opportunity=opportunity, accepted=True)
        access1.completed_learn_date = now() - timedelta(days=1)
        access1.save()

        access2 = OpportunityAccessFactory(opportunity=opportunity, accepted=True)
        access2.completed_learn_date = now() - timedelta(days=2)
        access2.save()

        AssessmentFactory(opportunity_access=access1, opportunity=opportunity, passed=True)
        AssessmentFactory(opportunity_access=access2, opportunity=opportunity, passed=True)

        issue_user_credentials()

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
            delivery_level=DeliveryLevel.FIFTY,
        )
        issue_user_credentials()

        expected_users_earning_credentials = {access2.user.id}
        assert (
            set(UserCredential.objects.all().values_list("user_id", flat=True)) == expected_users_earning_credentials
        )


@pytest.mark.django_db
class TestGetLearningUserCredentials:
    def test_no_credentials_issued_yet(self, opportunity):
        cred_config = CredentialConfigurationFactory(
            opportunity=opportunity,
            learn_level=LearnLevel.LEARN_PASSED,
        )

        access = OpportunityAccessFactory(opportunity=opportunity, accepted=True)
        access.completed_learn_date = now() - timedelta(days=1)
        access.save()

        AssessmentFactory(opportunity_access=access, opportunity=opportunity, passed=True)

        users_earning_creds = get_learning_user_credentials(cred_config)
        assert len(users_earning_creds) == 1

        cred_to_be_issued = users_earning_creds[0]
        assert cred_to_be_issued.user_id == access.user.id
        assert cred_to_be_issued.opportunity == opportunity
        assert cred_to_be_issued.level == LearnLevel.LEARN_PASSED
        assert cred_to_be_issued.credential_type == UserCredential.CredentialType.LEARN
        assert cred_to_be_issued.delivery_type == opportunity.delivery_type

    def test_existing_credential_users_skipped(self, opportunity):
        cred_config = CredentialConfigurationFactory(
            opportunity=opportunity,
            learn_level=LearnLevel.LEARN_PASSED,
        )

        access1 = OpportunityAccessFactory(opportunity=opportunity, accepted=True)
        access1.completed_learn_date = now() - timedelta(days=1)
        access1.save()

        AssessmentFactory(opportunity_access=access1, opportunity=opportunity, passed=True)

        UserCredentialFactory(
            user=access1.user,
            opportunity=opportunity,
            credential_type=UserCredential.CredentialType.LEARN,
            level=LearnLevel.LEARN_PASSED,
        )
        assert UserCredential.objects.count() == 1

        users_earning_creds = get_learning_user_credentials(cred_config)
        assert len(users_earning_creds) == 0

        access2 = OpportunityAccessFactory(opportunity=opportunity, accepted=True)
        access2.completed_learn_date = now() - timedelta(days=1)
        access2.save()

        AssessmentFactory(opportunity_access=access2, opportunity=opportunity, passed=True)

        access3 = OpportunityAccessFactory(opportunity=opportunity, accepted=True)
        access3.completed_learn_date = now() - timedelta(days=1)
        access3.save()

        AssessmentFactory(opportunity_access=access3, opportunity=opportunity, passed=False)

        users_earning_creds = get_learning_user_credentials(cred_config)
        assert len(users_earning_creds) == 1
        assert users_earning_creds[0].user_id == access2.user.id


@pytest.mark.django_db
class TestGetDeliveryUserCredentials:
    def test_no_credentials_issued_yet(self, opportunity):
        access1 = OpportunityAccessFactory(opportunity=opportunity, accepted=True)
        access2 = OpportunityAccessFactory(opportunity=opportunity, accepted=True)

        CompletedWorkFactory.create_batch(50, opportunity_access=access1, status=CompletedWorkStatus.approved)
        CompletedWorkFactory.create_batch(5, opportunity_access=access2, status=CompletedWorkStatus.approved)

        assert UserCredential.objects.count() == 0

        cred_config = CredentialConfigurationFactory(
            opportunity=opportunity,
            delivery_level=DeliveryLevel.FIFTY,
        )
        # Refresh from db to convert enum to string value
        cred_config.refresh_from_db()

        users_earning_creds = get_delivery_user_credentials(cred_config)
        assert len(users_earning_creds) == 1

        cred_to_be_issued = users_earning_creds[0]
        assert cred_to_be_issued.user_id == access1.user.id
        assert cred_to_be_issued.opportunity == opportunity
        assert cred_to_be_issued.level == DeliveryLevel.FIFTY
        assert cred_to_be_issued.credential_type == UserCredential.CredentialType.DELIVERY
        assert cred_to_be_issued.delivery_type == opportunity.delivery_type

    def test_existing_credential_users_skipped(self, opportunity):
        cred_config = CredentialConfigurationFactory(
            opportunity=opportunity,
            delivery_level=DeliveryLevel.FIFTY,
        )
        cred_config.refresh_from_db()

        access1 = OpportunityAccessFactory(opportunity=opportunity, accepted=True)
        CompletedWorkFactory.create_batch(50, opportunity_access=access1, status=CompletedWorkStatus.approved)

        UserCredentialFactory(
            user=access1.user,
            opportunity=opportunity,
            credential_type=UserCredential.CredentialType.DELIVERY,
            level=DeliveryLevel.FIFTY,
        )
        assert UserCredential.objects.count() == 1

        users_earning_creds = get_delivery_user_credentials(cred_config)
        assert len(users_earning_creds) == 0

        access2 = OpportunityAccessFactory(opportunity=opportunity, accepted=True)
        CompletedWorkFactory.create_batch(50, opportunity_access=access2, status=CompletedWorkStatus.approved)

        users_earning_creds = get_delivery_user_credentials(cred_config)
        assert len(users_earning_creds) == 1
        assert users_earning_creds[0].user_id == access2.user.id
