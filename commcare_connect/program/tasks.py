from allauth.utils import build_absolute_uri
from django.db.models import F
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.translation import gettext as _

from commcare_connect.opportunity.models import (
    CompletedWork,
    CompletedWorkStatus,
    Opportunity,
    VisitReviewStatus,
    VisitValidationStatus,
)
from commcare_connect.organization.models import Organization, UserOrganizationMembership
from commcare_connect.program.models import ManagedOpportunity, ProgramApplication
from commcare_connect.utils.tasks import send_mail_async
from config import celery_app


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

    send_mail_async.delay(
        subject=subject,
        message=message,
        recipient_list=recipient_emails,
        html_message=html_message,
    )


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

    send_mail_async.delay(
        subject=subject,
        message=message,
        recipient_list=recipient_emails,
        html_message=html_message,
    )


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

    send_mail_async.delay(
        subject=subject,
        message=message,
        recipient_list=recipient_emails,
        html_message=html_message,
    )


def _get_membership_users_emails(organization):
    recipient_emails = UserOrganizationMembership.objects.filter(organization=organization).values_list(
        "user__email", flat=True
    )
    return [email for email in recipient_emails if email]


def _get_program_home_url(org_slug):
    return build_absolute_uri(None, reverse("program:home", kwargs={"org_slug": org_slug}))


@celery_app.task()
def send_monthly_delivery_reminder_email():
    organizations_with_pending_deliveries = Organization.objects.filter(
        opportunity__opportunityaccess__completedwork__status=CompletedWorkStatus.pending
    ).distinct()

    for organization in organizations_with_pending_deliveries.iterator(chunk_size=50):
        send_nm_reminder_for_opportunities(
            organization=organization,
        )
        send_pm_reminder_for_opportunities(
            organization=organization,
        )


def send_nm_reminder_for_opportunities(organization):
    opp_ids_pending_review = (
        CompletedWork.objects.filter(
            opportunity_access__opportunity__organization=organization,
            uservisit__status=VisitValidationStatus.pending,
        )
        .values_list("opportunity_access__opportunity_id", flat=True)
        .distinct()
    )

    if not opp_ids_pending_review:
        return

    opportunities = organization.opportunities.filter(
        id__in=opp_ids_pending_review,
    ).only("name", "id")

    _send_org_email_for_opportunities(
        organization=organization,
        opportunities=opportunities,
        recipient_emails=_get_membership_users_emails(organization),
    )


def send_pm_reminder_for_opportunities(organization):
    opp_ids_pending_pm_review = (
        CompletedWork.objects.filter(
            opportunity_access__opportunity__organization=organization,
            opportunity_access__opportunity__managed=True,
            uservisit__review_status=VisitReviewStatus.pending,
            uservisit__status=VisitValidationStatus.approved,
        )
        .values_list("opportunity_access__opportunity_id", flat=True)
        .distinct()
    )

    if not opp_ids_pending_pm_review:
        return

    opportunities = Opportunity.objects.filter(id__in=opp_ids_pending_pm_review).annotate(
        program_organization=F("managedopportunity__program__organization")
    )

    recipient_emails = set()
    for opp in opportunities:
        recipient_emails.update(_get_membership_users_emails(opp.program_organization))

    _send_org_email_for_opportunities(
        organization=organization,
        opportunities=opportunities,
        recipient_emails=list(recipient_emails),
    )


def _send_org_email_for_opportunities(organization, opportunities, recipient_emails):
    if not recipient_emails:
        return

    opportunity_links = []
    for opportunity in opportunities:
        worker_deliver_url = build_absolute_uri(
            None,
            reverse("opportunity:worker_deliver", kwargs={"org_slug": organization.slug, "opp_id": opportunity.id}),
        )
        opportunity_links.append({"name": opportunity.name, "url": worker_deliver_url})

    context = {
        "organization": organization,
        "opportunities": opportunity_links,
    }

    message = render_to_string("program/email/monthly_delivery_reminder.txt", context)
    html_message = render_to_string("program/email/monthly_delivery_reminder.html", context)

    send_mail_async.delay(
        subject=_("Reminder: Please Review Pending Deliveries"),
        message=message,
        recipient_list=recipient_emails,
        html_message=html_message,
    )
