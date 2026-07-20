from __future__ import annotations

import csv
import datetime

from django.db import transaction
from django.utils.translation import gettext

from commcare_connect.audit import calculations
from commcare_connect.audit.calculations import get_registered_calculations
from commcare_connect.audit.models import AuditReport, AuditReportEntry
from commcare_connect.opportunity.models import Opportunity, OpportunityAccess
from commcare_connect.utils.file import EchoWriter

STREAM_CHUNK_SIZE = 2000


def stream_audit_report_csv(report, selected_workers=None):
    # A single entry is enough to determine the columns -- no need to load them all.
    columns = column_specs(report.entries.all()[:1])
    writer = csv.writer(EchoWriter())

    yield writer.writerow([gettext("Connect Worker"), *_column_headers(columns)])

    entries = entries_for_export(report, selected_workers)
    for entry in entries.iterator(chunk_size=STREAM_CHUNK_SIZE):
        cells = [_export_cell_value(entry.results, name) for name, _label, _tooltip in columns]
        yield writer.writerow([entry.opportunity_access.user.name, *cells])


def _column_headers(columns):
    """Header label per calculation, with its acceptable range appended when known."""
    calcs = {c.name: c for c in get_registered_calculations()}
    headers = []
    for name, label, _tooltip in columns:
        reference_range = _format_reference_range(calcs[name]) if name in calcs else ""
        headers.append(f"{label} ({reference_range})" if reference_range else label)
    return headers


def _format_reference_range(calc):
    """Human-readable acceptable range for a calculation, e.g. "0.5 - 1.0", ">= 3", "<= 0.2"."""
    lower, upper = calc.lower_bound, calc.upper_bound
    if lower is not None and upper is not None:
        return f"{lower} - {upper}"
    if lower is not None:
        return f">= {lower}"
    if upper is not None:
        return f"<= {upper}"
    return ""


def _export_cell_value(results, calc_name):
    result = results.get(calc_name, {})
    if not result.get("has_sufficient_data"):
        return gettext("N/A")
    return result.get("value")


def column_specs(entries):
    """Calculation columns to render, ordered by registry then by appearance."""
    registry = {c.name: c.tooltip for c in get_registered_calculations()}
    seen = {}
    for entry in entries:
        for name, result in entry.results.items():
            if name not in seen:
                seen[name] = result.get("label", name)
    ordered = [(name, seen[name], registry.get(name, "")) for name in registry if name in seen]
    leftovers = [(name, label, "") for name, label in seen.items() if name not in registry]
    return ordered + leftovers


def entries_for_export(report, selected_workers=None):
    """Report entries for CSV export, filtered to match the detail view, ordered by name."""
    rows = report.entries.select_related("opportunity_access__user")
    if selected_workers:
        rows = rows.filter(opportunity_access_id__in=selected_workers)
    return rows.order_by("opportunity_access__user__name")


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
