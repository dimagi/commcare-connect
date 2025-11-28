from django.conf import settings
from django.core.mail import send_mail

from config import celery_app


@celery_app.task()
def send_mail_async(subject, message, recipient_list, from_email=settings.DEFAULT_FROM_EMAIL, html_message=None):
    send_mail(
        subject=subject,
        message=message,
        recipient_list=recipient_list,
        from_email=from_email,
        html_message=html_message,
    )
