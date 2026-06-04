from unittest import mock

import pytest

from commcare_connect.utils.sms import SMSException, get_sms_sender, send_sms


@pytest.mark.parametrize(
    "number,expected",
    [
        ("+265123456", "ConnectID"),
        ("+258999000", "ConnectID"),
        ("+232111222", "ConnectID"),
        ("+44777888999", "ConnectID"),
        ("+15555550123", None),
        ("+27821234567", None),
    ],
)
def test_get_sms_sender(number, expected):
    assert get_sms_sender(number) == expected


def test_send_sms_raises_without_credentials(settings):
    settings.TWILIO_ACCOUNT_SID = None
    settings.TWILIO_AUTH_TOKEN = "token"
    settings.TWILIO_MESSAGING_SERVICE = "svc"
    with pytest.raises(SMSException):
        send_sms(to="+15555550123", body="hi")


@mock.patch("commcare_connect.utils.sms.Client")
def test_send_sms_invokes_twilio_client(mock_client_cls, settings):
    """Pins the twilio call surface (Client init + messages.create kwargs) so a
    breaking twilio major bump is caught in CI rather than only in staging."""
    settings.TWILIO_ACCOUNT_SID = "sid"
    settings.TWILIO_AUTH_TOKEN = "token"
    settings.TWILIO_MESSAGING_SERVICE = "MGxxxx"
    messages = mock_client_cls.return_value.messages

    result = send_sms(to="+265123456", body="test message")

    mock_client_cls.assert_called_once_with("sid", "token")
    messages.create.assert_called_once()
    kwargs = messages.create.call_args.kwargs
    assert kwargs["body"] == "test message"
    assert kwargs["to"] == "+265123456"
    assert kwargs["from_"] == "ConnectID"  # +265 maps to the ConnectID alpha sender
    assert kwargs["messaging_service_sid"] == "MGxxxx"
    assert "sms_status_callback" in kwargs["status_callback"]
    assert result is messages.create.return_value
