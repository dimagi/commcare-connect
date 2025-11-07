from allauth.utils import build_absolute_uri
from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.urls import reverse

from commcare_connect.organization.models import UserOrganizationMembership
from commcare_connect.program.models import ManagedOpportunity, ProgramApplication
from config import celery_app


@celery_app.task()
def send_program_invite_applied_email(application_id):
    application = ProgramApplication.objects.select_related("program", "organization").get(pk=application_id)
    pm_org = application.program.organization
    recipient_emails = _get_membership_users_emails(pm_org)
    if not recipient_emails:
        return

    subject = f"Network Manager Applied for Program: {application.program.name}"

    context = {
        "application": application,
        "program_url": _get_program_home_url(pm_org.slug),
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


@celery_app.task()
def send_program_invite_email(application_id):
    application = ProgramApplication.objects.select_related("program", "organization").get(pk=application_id)
    nm_org = application.organization
    recipient_emails = _get_membership_users_emails(nm_org)
    if not recipient_emails:
        return

    subject = f"Invitation to Program: {application.program.name}"
    context = {
        "application": application,
        "program_url": _get_program_home_url(nm_org.slug),
    }
    message = render_to_string("program/email/program_invite_notification.txt", context)
    html_message = render_to_string("program/email/program_invite_notification.html", context)

    send_mail(
        subject=subject,
        message=message,
        recipient_list=recipient_emails,
        from_email=settings.DEFAULT_FROM_EMAIL,
        html_message=html_message,
    )


@celery_app.task()
def send_opportunity_created_email(opportunity_id):
    opportunity = ManagedOpportunity.objects.select_related("program", "organization").get(pk=opportunity_id)
    nm_org = opportunity.organization
    recipient_emails = _get_membership_users_emails(nm_org)
    if not recipient_emails:
        return

    opportunity_url = build_absolute_uri(
        None, reverse("opportunity:detail", kwargs={"org_slug": nm_org.slug, "opp_id": opportunity_id})
    )

    subject = f"New Opportunity Created: {opportunity.name}"
    context = {
        "opportunity": opportunity,
        "opportunity_url": opportunity_url,
    }

    message = render_to_string("program/email/opportunity_created.txt", context)
    html_message = render_to_string("program/email/opportunity_created.html", context)

    send_mail(
        subject=subject,
        message=message,
        recipient_list=recipient_emails,
        from_email=settings.DEFAULT_FROM_EMAIL,
        html_message=html_message,
    )


def _get_membership_users_emails(organization):
    recipient_emails = UserOrganizationMembership.objects.filter(organization=organization).values_list(
        "user__email", flat=True
    )
    return [email for email in recipient_emails if email]


def _get_program_home_url(org_slug):
    return build_absolute_uri(None, reverse("program:home", kwargs={"org_slug": org_slug}))
