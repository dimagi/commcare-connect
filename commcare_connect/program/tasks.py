import datetime
from collections import defaultdict

from allauth.utils import build_absolute_uri
from django.db.models import Q
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.translation import gettext as _
from django.utils.translation import ngettext

from commcare_connect.opportunity.models import (
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
    # Find organizations with pending delivery review or pending managed delivery reviews
    organizations_with_pending_deliveries = Organization.objects.filter(
        Q(opportunity__opportunityaccess__completedwork__status=CompletedWorkStatus.pending)
        | Q(
            program__managedopportunity__opportunityaccess__completedwork__uservisit__review_status=VisitReviewStatus.pending  # noqa:E501
        ),
    ).distinct()

    for organization in organizations_with_pending_deliveries.iterator(chunk_size=50):
        opportunities = get_org_opps_for_review(organization)

        if organization.program_manager:
            opportunities.extend(get_org_managed_opps_for_review(organization))

        if not opportunities:
            continue

        _send_org_email_for_opportunities(
            organization=organization,
            opportunities=opportunities,
            recipient_emails=_get_membership_users_emails(organization),
        )


def get_org_opps_for_review(organization):
    return list(
        Opportunity.objects.filter(
            organization=organization,
            opportunityaccess__completedwork__uservisit__status=VisitValidationStatus.pending,
            is_test=False,
        )
        .distinct()
        .only("name", "id")
    )


def get_org_managed_opps_for_review(organization):
    return list(
        Opportunity.objects.filter(
            managed=True,
            managedopportunity__program__organization=organization,
            opportunityaccess__completedwork__uservisit__review_status=VisitReviewStatus.pending,
            opportunityaccess__completedwork__uservisit__status=VisitValidationStatus.approved,
            is_test=False,
        )
        .distinct()
        .only("id", "name")
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


def send_opportunity_expiry_reminder_emails(days_before: int):
    target_date = datetime.date.today() + datetime.timedelta(days=days_before)
    opportunities = ManagedOpportunity.objects.filter(end_date=target_date, active=True).select_related(
        "program__organization", "organization"
    )

    # Group by PM org
    pm_orgs = {}
    pm_org_opportunities = defaultdict(list)
    for opp in opportunities:
        pm_org = opp.program.organization
        pm_orgs[pm_org.id] = pm_org
        opportunity_url = build_absolute_uri(
            None,
            reverse(
                "opportunity:detail",
                kwargs={"org_slug": pm_org.slug, "opp_id": opp.id},
            ),
        )
        pm_org_opportunities[pm_org.id].append(
            {
                "name": opp.name,
                "end_date": opp.end_date,
                "program_name": opp.program.name,
                "org_name": opp.organization.name,
                "opportunity_url": opportunity_url,
            }
        )

    if not pm_org_opportunities:
        return

    # Fetch all membership emails in a single query
    org_emails = defaultdict(list)
    for org_id, email in UserOrganizationMembership.objects.filter(
        organization_id__in=pm_org_opportunities.keys()
    ).values_list("organization_id", "user__email"):
        if email:
            org_emails[org_id].append(email)

    for org_id, opps in pm_org_opportunities.items():
        pm_org = pm_orgs[org_id]
        recipient_emails = org_emails[org_id]
        if not recipient_emails:
            continue

        opp_count = len(opps)
        date_str = target_date.strftime("%d %b %Y")
        subject = ngettext(
            "Reminder: %(count)d opportunity ending on %(date)s",
            "Reminder: %(count)d opportunities ending on %(date)s",
            opp_count,
        ) % {"count": opp_count, "date": date_str}
        context = {
            "target_date": target_date,
            "opportunities": opps,
            "organization": pm_org,
        }

        message = render_to_string("program/email/opportunity_expiry_reminder.txt", context)
        html_message = render_to_string("program/email/opportunity_expiry_reminder.html", context)

        send_mail_async.delay(
            subject=subject,
            message=message,
            recipient_list=recipient_emails,
            html_message=html_message,
        )


@celery_app.task()
def send_opportunity_expiry_reminders():
    send_opportunity_expiry_reminder_emails(7)
    send_opportunity_expiry_reminder_emails(3)
