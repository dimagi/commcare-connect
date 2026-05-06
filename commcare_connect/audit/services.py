from __future__ import annotations

import datetime

from django.db import transaction

from commcare_connect.audit import calculations
from commcare_connect.audit.models import AuditReport, AuditReportEntry
from commcare_connect.opportunity.models import Opportunity, OpportunityAccess


def period_for(today: datetime.date) -> tuple[datetime.date, datetime.date]:
    """Return the most recently completed Monday-Sunday week strictly before `today`.

    Stable regardless of which weekday the task runs on: calling this on any day
    always returns the previous full Mon-Sun window, never a current/in-progress one.
    """
    # Monday = 0 ... Sunday = 6. Days since the most recent *completed* Sunday:
    # Mon=1, Tue=2, ..., Sat=6, Sun=7 (the Sunday a week ago, not today).
    days_since_last_sunday = today.weekday() + 1
    period_end = today - datetime.timedelta(days=days_since_last_sunday)
    period_start = period_end - datetime.timedelta(days=6)
    return period_start, period_end


@transaction.atomic
def generate_audit_report_for_opportunity(
    opportunity: Opportunity,
    period_start: datetime.date,
    period_end: datetime.date,
) -> AuditReport:
    report = AuditReport.objects.create(
        opportunity=opportunity,
        period_start=period_start,
        period_end=period_end,
    )

    active_accesses = (
        OpportunityAccess.objects.filter(
            opportunity=opportunity,
            accepted=True,
            suspended=False,
        )
        .select_related("user")
        .order_by("user__name")
    )

    calcs = calculations.get_registered_calculations()
    entries = []
    for access in active_accesses:
        results = {}
        flagged = False
        for calc in calcs:
            result = calc.run(access, period_start, period_end)
            results[result.name] = result.to_dict()
            if result.has_sufficient_data and not result.in_range:
                flagged = True
        entries.append(
            AuditReportEntry(
                audit_report=report,
                opportunity_access=access,
                results=results,
                flagged=flagged,
            )
        )

    if entries:
        AuditReportEntry.objects.bulk_create(entries)

    return report
