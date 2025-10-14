from allauth.utils import build_absolute_uri
from django.conf import settings
from django.urls import reverse
from twilio.rest import Client


class SMSException(Exception):
    pass


def send_sms(to, body):
    if not (settings.TWILIO_ACCOUNT_SID and settings.TWILIO_AUTH_TOKEN and settings.TWILIO_MESSAGING_SERVICE):
        raise SMSException("Twilio credentials not provided")
    client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
    sender = get_sms_sender(to)
    return client.messages.create(
        body=body,
        to=to,
        from_=sender,
        messaging_service_sid=settings.TWILIO_MESSAGING_SERVICE,
        status_callback=build_absolute_uri(None, reverse("users:sms_status_callback")),
    )


def get_sms_sender(number):
    SMS_SENDERS = {"+265": "ConnectID", "+258": "ConnectID", "+232": "ConnectID", "+44": "ConnectID"}
    for code, sender in SMS_SENDERS.items():
        if number.startswith(code):
            return sender
    return None
