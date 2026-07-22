import datetime
import json

import httpx
import pytest
from allauth.socialaccount.models import SocialAccount, SocialApp, SocialToken
from django.test import override_settings
from django.utils import timezone

from commcare_connect.utils import ocs_api
from commcare_connect.utils.oauth_tokens import SocialTokenMissingError

OCS_URL = "https://ocs.example.test"


def _connect_ocs(user, *, access="access", expires_in_minutes=30):
    app = SocialApp.objects.create(provider="ocs", name="ocs", client_id="ocs-client", secret="ocs-secret")
    account = SocialAccount.objects.create(user=user, provider="ocs", uid="uid-ocs")
    SocialToken.objects.create(
        app=app,
        account=account,
        token=access,
        token_secret="refresh",
        expires_at=timezone.now() + datetime.timedelta(minutes=expires_in_minutes),
    )


@pytest.mark.django_db
def test_user_has_connected_ocs_reflects_account(user):
    assert ocs_api.user_has_connected_ocs(user) is False
    _connect_ocs(user)
    assert ocs_api.user_has_connected_ocs(user) is True


@pytest.mark.django_db
@override_settings(OCS_BASE_URL=OCS_URL)
def test_list_chatbots_follows_cursor_pagination(user, httpx_mock):
    _connect_ocs(user)
    httpx_mock.add_response(
        url=f"{OCS_URL}/api/v2/chatbots/",
        json={
            "count": 2,
            "next": f"{OCS_URL}/api/v2/chatbots/?cursor=abc",
            "previous": None,
            "results": [{"id": "id-1", "name": "Bot One"}],
        },
    )
    httpx_mock.add_response(
        url=f"{OCS_URL}/api/v2/chatbots/?cursor=abc",
        json={"count": 2, "next": None, "previous": None, "results": [{"id": "id-2", "name": "Bot Two"}]},
    )

    result = ocs_api.list_chatbots(user)

    assert result == [("id-1", "Bot One"), ("id-2", "Bot Two")]


@pytest.mark.django_db
@override_settings(OCS_BASE_URL=OCS_URL)
def test_list_chatbots_sends_bearer_token(user, httpx_mock):
    _connect_ocs(user, access="the-access-token")
    httpx_mock.add_response(
        url=f"{OCS_URL}/api/v2/chatbots/",
        json={"count": 0, "next": None, "previous": None, "results": []},
    )

    ocs_api.list_chatbots(user)

    request = httpx_mock.get_requests()[0]
    assert request.headers["Authorization"] == "Bearer the-access-token"


@pytest.mark.django_db
@override_settings(OCS_BASE_URL=OCS_URL)
def test_list_chatbots_raises_ocs_api_error_on_non_2xx(user, httpx_mock):
    _connect_ocs(user)
    httpx_mock.add_response(url=f"{OCS_URL}/api/v2/chatbots/", status_code=500, text="boom")

    with pytest.raises(ocs_api.OcsApiError):
        ocs_api.list_chatbots(user)


@pytest.mark.django_db
@override_settings(OCS_BASE_URL=OCS_URL)
def test_list_chatbots_raises_ocs_api_error_on_network_error(user, httpx_mock):
    _connect_ocs(user)
    httpx_mock.add_exception(httpx.ConnectError("boom"), url=f"{OCS_URL}/api/v2/chatbots/")
    with pytest.raises(ocs_api.OcsApiError):
        ocs_api.list_chatbots(user)


@pytest.mark.django_db
@override_settings(OCS_BASE_URL=OCS_URL)
def test_list_chatbots_raises_when_not_connected(user):
    with pytest.raises(SocialTokenMissingError):
        ocs_api.list_chatbots(user)


@pytest.mark.django_db
@override_settings(OCS_BASE_URL=OCS_URL)
def test_trigger_bot_posts_required_payload_and_maps_response(user, httpx_mock):
    _connect_ocs(user)
    httpx_mock.add_response(
        url=f"{OCS_URL}/api/trigger_bot",
        method="POST",
        json={
            "session_id": "s1",
            "url": "https://ocs/x",
            "team": {"id": "t"},
            "channel": {"data": {"external_channel_id": "chan-1"}},
        },
    )

    session_id, channel_id = ocs_api.trigger_bot(
        user,
        identifier="flw-user",
        experiment="exp-uuid",
    )

    assert session_id == "s1"
    assert channel_id == "chan-1"
    body = json.loads(httpx_mock.get_requests()[0].read())
    assert body["identifier"] == "flw-user"
    assert body["experiment"] == "exp-uuid"
    assert body["platform"] == "commcare_connect"
    assert body["start_new_session"] is True


@pytest.mark.django_db
@override_settings(OCS_BASE_URL=OCS_URL)
def test_trigger_bot_forwards_participant_data(user, httpx_mock):
    _connect_ocs(user)
    httpx_mock.add_response(
        url=f"{OCS_URL}/api/trigger_bot",
        method="POST",
        json={"session_id": "s", "url": "u", "team": {}, "channel": {"data": {"external_channel_id": "c"}}},
    )

    ocs_api.trigger_bot(user, identifier="flw", experiment="exp", participant_data={"connectTaskId": "task-1"})

    body = json.loads(httpx_mock.get_requests()[0].read())
    assert body["participant_data"] == {"connectTaskId": "task-1"}


@pytest.mark.django_db
@override_settings(OCS_BASE_URL=OCS_URL)
def test_trigger_bot_includes_optionals_only_when_provided(user, httpx_mock):
    _connect_ocs(user)
    httpx_mock.add_response(
        url=f"{OCS_URL}/api/trigger_bot",
        method="POST",
        json={"session_id": "s", "url": "u", "team": {}, "channel": {"data": {"external_channel_id": "c"}}},
    )

    ocs_api.trigger_bot(user, identifier="flw", experiment="exp", start_new_session=True)

    body = json.loads(httpx_mock.get_requests()[0].read())
    assert body["start_new_session"] is True


@pytest.mark.django_db
@override_settings(OCS_BASE_URL=OCS_URL)
def test_trigger_bot_includes_falsey_optionals_when_provided(user, httpx_mock):
    """A falsey-but-not-None optional (e.g. start_new_session=False) must survive into the payload."""
    _connect_ocs(user)
    httpx_mock.add_response(
        url=f"{OCS_URL}/api/trigger_bot",
        method="POST",
        json={"session_id": "s", "url": "u", "team": {}, "channel": {"data": {"external_channel_id": "c"}}},
    )

    ocs_api.trigger_bot(user, identifier="flw", experiment="exp", start_new_session=False)

    body = json.loads(httpx_mock.get_requests()[0].read())
    assert "start_new_session" in body
    assert body["start_new_session"] is False


@pytest.mark.django_db
@override_settings(OCS_BASE_URL=OCS_URL)
@pytest.mark.parametrize(
    "response_json",
    [
        {"session_id": "s", "channel": {"data": {}}},
        {"channel": {"data": {"external_channel_id": "c"}}},
        {"session_id": "s"},
    ],
)
def test_trigger_bot_raises_when_session_or_channel_missing(user, httpx_mock, response_json):
    _connect_ocs(user)
    httpx_mock.add_response(url=f"{OCS_URL}/api/trigger_bot", method="POST", json=response_json)

    with pytest.raises(ocs_api.OcsApiError):
        ocs_api.trigger_bot(user, identifier="flw", experiment="exp")


@pytest.mark.django_db
@override_settings(OCS_BASE_URL=OCS_URL)
def test_trigger_bot_raises_ocs_api_error_on_non_2xx(user, httpx_mock):
    _connect_ocs(user)
    httpx_mock.add_response(url=f"{OCS_URL}/api/trigger_bot", method="POST", status_code=400, text="bad")

    with pytest.raises(ocs_api.OcsApiError):
        ocs_api.trigger_bot(user, identifier="flw", experiment="exp")


@pytest.mark.django_db
@override_settings(OCS_BASE_URL=OCS_URL)
def test_trigger_bot_raises_ocs_api_error_on_network_error(user, httpx_mock):
    _connect_ocs(user)
    httpx_mock.add_exception(httpx.ConnectError("boom"), url=f"{OCS_URL}/api/trigger_bot", method="POST")
    with pytest.raises(ocs_api.OcsApiError):
        ocs_api.trigger_bot(user, identifier="flw", experiment="exp")
