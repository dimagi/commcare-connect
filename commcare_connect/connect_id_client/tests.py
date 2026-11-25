from httpx import URL

from .main import fetch_users, queue_message_bulk, send_message
from .models import FCM_ANALYTICS_LABEL, Message, MessageStatus


def test_fetch_users(httpx_mock):
    httpx_mock.add_response(
        method="GET",
        json={
            "found_users": [
                {
                    "username": "user_name1",
                    "name": "name1",
                    "phone_number": "phone_number1",
                },
                {
                    "name": "name2",
                    "username": "user_name2",
                    "phone_number": "phone_number2",
                },
            ]
        },
    )

    users = fetch_users(["phone_number1", "phone_number2"])
    assert len(users) == 2
    assert users[0].name == "name1"
    assert users[1].name == "name2"

    request = httpx_mock.get_request()
    assert URL(request.url).params.get_list("phone_numbers") == ["phone_number1", "phone_number2"]


def test_send_message(httpx_mock):
    httpx_mock.add_response(
        method="POST",
        match_json={
            "usernames": ["user_name1"],
            "body": "test message",
            "fcm_options": {"analytics_label": FCM_ANALYTICS_LABEL},
        },
        json={"all_success": True, "responses": [{"username": "user_name1", "status": "success"}]},
    )
    result = send_message(Message(usernames=["user_name1"], body="test message"))
    assert result.all_success is True
    assert len(result.responses) == 1
    assert result.responses[0].username == "user_name1"
    assert result.responses[0].status == MessageStatus.success


def test_queue_message_bulk(httpx_mock):
    httpx_mock.add_response(
        match_json={
            "messages": [
                {
                    "usernames": ["user_name1"],
                    "body": "test message1",
                    "fcm_options": {"analytics_label": FCM_ANALYTICS_LABEL},
                },
            ],
            "async": True,
        },
        status_code=202,
        json={"queued": True, "messages": 1},
    )

    assert queue_message_bulk([Message(usernames=["user_name1"], body="test message1")]) is None


def test_queue_message_bulk_accepts_synchronous_response(httpx_mock):
    # A ConnectID that predates "async" ignores it and replies with results.
    httpx_mock.add_response(json={"all_success": True, "messages": []})

    queue_message_bulk([Message(usernames=["user_name1"], body="test message1")])


def test_queue_message_bulk_without_messages_makes_no_request(httpx_mock):
    queue_message_bulk([])

    assert not httpx_mock.get_requests()
