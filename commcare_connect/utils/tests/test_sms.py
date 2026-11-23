from unittest import mock

import pytest

from commcare_connect.utils.sms import SMSException, is_sms_destination_allowed, send_sms


@pytest.mark.parametrize(
    "allowed_codes, number, expected",
    [
        ([], "+79001234567", True),
        (["+265", "+258"], "+265991234567", True),
        (["+265", "+258"], "+79001234567", False),
    ],
)
def test_is_sms_destination_allowed(settings, allowed_codes, number, expected):
    settings.SMS_ALLOWED_COUNTRY_CODES = allowed_codes
    assert is_sms_destination_allowed(number) is expected


def test_send_sms_refuses_disallowed_country(settings):
    settings.TWILIO_ACCOUNT_SID = "sid"
    settings.TWILIO_AUTH_TOKEN = "token"
    settings.TWILIO_MESSAGING_SERVICE = "service"
    settings.SMS_ALLOWED_COUNTRY_CODES = ["+265"]
    with mock.patch("commcare_connect.utils.sms.Client") as client, pytest.raises(SMSException):
        send_sms("+79001234567", "hello")
    client.assert_not_called()
