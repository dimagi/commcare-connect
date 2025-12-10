import datetime
from unittest import mock

import pytest
from django.utils.timezone import now

from commcare_connect.connect_id_client.models import ConnectIdUser
from commcare_connect.opportunity.models import BlobMeta, Opportunity, OpportunityAccess
from commcare_connect.opportunity.tasks import (
    _get_inactive_message,
    add_connect_users,
    download_user_visit_attachments,
)
from commcare_connect.opportunity.tests.factories import (
    CompletedModuleFactory,
    LearnModuleFactory,
    OpportunityClaimFactory,
    OpportunityFactory,
    UserVisitFactory,
)
from commcare_connect.users.models import User


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
    assert message.data.get("title") == f"Resume your learning journey for {opportunity.name}"


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
    assert message.data.get("title") == f"Resume your job for {opportunity.name}"


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
    assert message.data.get("title") == f"Resume your job for {opportunity.name}"


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
