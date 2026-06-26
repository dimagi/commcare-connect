from __future__ import annotations

import logging
from collections import defaultdict

from allauth.utils import build_absolute_uri
from django.db.models import Q
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from commcare_connect.audit.services import generate_audit_report_for_opportunity, period_for
from commcare_connect.flags.flag_names import WEEKLY_PERFORMANCE_REPORT
from commcare_connect.flags.models import Flag
from commcare_connect.opportunity.models import Opportunity
from commcare_connect.organization.models import UserOrganizationMembership
from commcare_connect.utils.tasks import send_mail_async
from config.celery_app import app

logger = logging.getLogger(__name__)


@app.task(name="audit.generate_audit_reports")
def generate_audit_reports() -> None:
    try:
        flag = Flag.objects.get(name=WEEKLY_PERFORMANCE_REPORT)
    except Flag.DoesNotExist:
        logger.info("WEEKLY_PERFORMANCE_REPORT flag is not configured; nothing to do.")
        return

    period_start, period_end = period_for(timezone.now().date())

    opportunities = Opportunity.objects.filter(
        Q(pk__in=flag.opportunities.values_list("pk", flat=True)) | Q(program__in=flag.programs.all()),
        active=True,
    ).distinct()

    generated_reports = []
    for opportunity in opportunities:
        try:
            report = generate_audit_report_for_opportunity(
                opportunity,
                period_start=period_start,
                period_end=period_end,
            )
        except Exception:
            logger.exception("Failed to generate weekly report for opportunity %s", opportunity.pk)
            continue
        generated_reports.append(report)

    try:
        send_new_audit_report_notifications(generated_reports)
    except Exception:
        logger.exception("Failed to send audit report notifications")


def send_new_audit_report_notifications(reports) -> None:
    """Email PM-org admins about newly generated audit reports.

    Reports are grouped by PM org so each org's admins receive a single email
    listing all of that org's new reports. Reports for opportunities without a
    program (no PM org) are skipped, as are orgs with no admin recipients.
    """
    pm_orgs = {}
    reports_by_org = defaultdict(list)
    for report in reports:
        opportunity = report.opportunity
        if opportunity.program_id is None:
            continue
        pm_org = opportunity.program.organization
        pm_orgs[pm_org.id] = pm_org
        report_url = build_absolute_uri(
            None,
            reverse(
                "opportunity:audit:audit_report_detail",
                kwargs={
                    "org_slug": pm_org.slug,
                    "opp_id": opportunity.opportunity_id,
                    "audit_report_id": report.audit_report_id,
                },
            ),
        )
        reports_by_org[pm_org.id].append(
            {
                "opportunity_name": opportunity.name,
                "period_start": report.period_start.isoformat(),
                "period_end": report.period_end.isoformat(),
                "url": report_url,
            }
        )

    if not reports_by_org:
        return

    org_emails = defaultdict(list)
    for org_id, email in UserOrganizationMembership.objects.filter(
        organization_id__in=reports_by_org.keys(),
        role=UserOrganizationMembership.Role.ADMIN,
    ).values_list("organization_id", "user__email"):
        if email:
            org_emails[org_id].append(email)

    for org_id, items in reports_by_org.items():
        recipient_emails = org_emails.get(org_id)
        if not recipient_emails:
            continue
        try:
            _send_org_audit_report_email(pm_orgs[org_id], items, recipient_emails)
        except Exception:
            logger.exception("Failed to send audit report notification for organization %s", org_id)


def _send_org_audit_report_email(organization, reports, recipient_emails) -> None:
    if len(reports) == 1:
        subject = f"New audit report for {reports[0]['opportunity_name']}"
    else:
        subject = f"{len(reports)} new audit reports are ready for review"

    context = {"organization": organization, "reports": reports}
    message = render_to_string("audit/email/new_audit_report_notification.txt", context)
    html_message = render_to_string("audit/email/new_audit_report_notification.html", context)

    send_mail_async.delay(
        subject=subject,
        message=message,
        recipient_list=recipient_emails,
        html_message=html_message,
    )
