from httpx import URL

from .main import fetch_users, send_message, send_message_bulk
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


def test_send_message_bulk(httpx_mock):
    httpx_mock.add_response(
        match_json={
            "messages": [
                {
                    "usernames": ["user_name1", "user_name2"],
                    "body": "test message1",
                    "fcm_options": {"analytics_label": FCM_ANALYTICS_LABEL},
                },
                {
                    "usernames": ["user_name3", "user_name4"],
                    "body": "test message2",
                    "fcm_options": {"analytics_label": FCM_ANALYTICS_LABEL},
                },
            ]
        },
        json={
            "all_success": False,
            "messages": [
                {
                    "all_success": True,
                    "responses": [
                        {"status": "success", "username": "user_name1"},
                        {"status": "success", "username": "user_name2"},
                    ],
                },
                {
                    "all_success": False,
                    "responses": [
                        {"status": "error", "username": "user_name3"},
                        {"status": "deactivated", "username": "user_name4"},
                    ],
                },
            ],
        },
    )

    result = send_message_bulk(
        [
            Message(usernames=["user_name1", "user_name2"], body="test message1"),
            Message(usernames=["user_name3", "user_name4"], body="test message2"),
        ]
    )
    assert result.all_success is False
    assert len(result.messages) == 2

    assert result.messages[0].all_success is True
    assert [(resp.username, resp.status) for resp in result.messages[0].responses] == [
        ("user_name1", MessageStatus.success),
        ("user_name2", MessageStatus.success),
    ]
    assert result.messages[0].responses[0].username == "user_name1"
    assert result.messages[0].responses[0].status == MessageStatus.success
    assert result.messages[0].responses[1].username == "user_name2"
    assert result.messages[0].responses[1].status == MessageStatus.success

    assert result.messages[1].all_success is False
    assert [(resp.username, resp.status) for resp in result.messages[1].responses] == [
        ("user_name3", MessageStatus.error),
        ("user_name4", MessageStatus.deactivated),
    ]

    assert result.get_failures() == [[], list(result.messages[1].responses)]
