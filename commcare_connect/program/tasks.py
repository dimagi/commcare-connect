from allauth.utils import build_absolute_uri
from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.urls import reverse

from commcare_connect.organization.models import UserOrganizationMembership
from commcare_connect.program.models import ProgramApplication
from config import celery_app


@celery_app.task()
def send_program_invite_applied_email(application_id):
    application = ProgramApplication.objects.select_related("program", "organization").get(pk=application_id)
    pm_org = application.program.organization
    recipient_emails = UserOrganizationMembership.objects.filter(organization=pm_org).values_list(
        "user__email", flat=True
    )
    recipient_emails = [email for email in recipient_emails if email]
    if not recipient_emails:
        return

    program_url = build_absolute_uri(None, reverse("program:home", kwargs={"org_slug": pm_org.slug}))

    subject = f"Network Manager Applied for Program: {application.program.name}"

    context = {
        "application": application,
        "program_url": program_url,
    }

    message = render_to_string("program/email/program_invite_applied.txt", context)
    html_message = render_to_string("program/email/program_invite_applied.html", context)

    send_mail(
        subject=subject,
        message=message,
        recipient_list=recipient_emails,
        from_email=settings.DEFAULT_FROM_EMAIL,
        html_message=html_message,
    )
