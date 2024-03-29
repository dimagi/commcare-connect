from django.conf import settings
from twilio.rest import Client


class SMSException(Exception):
    pass


def send_sms(to, body):
    if not (settings.TWILIO_ACCOUNT_SID and settings.TWILIO_AUTH_TOKEN and settings.TWILIO_MESSAGING_SERVICE):
        raise SMSException("Twilio credentials not provided")
    client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
    sender = get_sms_sender(to)
    client.messages.create(body=body, to=to, from_=sender, messaging_service_sid=settings.TWILIO_MESSAGING_SERVICE)


def get_sms_sender(number):
    SMS_SENDERS = {"+265": "ConnectID"}
    for code, sender in SMS_SENDERS.items():
        if number.startswith(code):
            return sender
    return None
