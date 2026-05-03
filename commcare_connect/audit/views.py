from django.contrib.auth.decorators import login_required
from django.db.models import F, Window
from django.db.models.functions import RowNumber
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django_tables2 import RequestConfig

from commcare_connect.audit.calculations import get_registered_calculations
from commcare_connect.audit.models import AuditReport
from commcare_connect.audit.tables import AuditReportEntryTable, AuditReportTable
from commcare_connect.flags.flag_names import WEEKLY_PERFORMANCE_REPORT
from commcare_connect.flags.models import Flag
from commcare_connect.opportunity.models import Opportunity
from commcare_connect.organization.decorators import org_program_manager_required

DEFAULT_PAGE_SIZE = 25


def _require_flagged_opportunity(org_slug, opportunity_id):
    opportunity = get_object_or_404(Opportunity, opportunity_id=opportunity_id, organization__slug=org_slug)
    try:
        flag = Flag.objects.get(name=WEEKLY_PERFORMANCE_REPORT)
    except Flag.DoesNotExist:
        raise Http404("Weekly performance report is not enabled.")
    if not flag.opportunities.filter(pk=opportunity.pk).exists():
        raise Http404("Weekly performance report is not enabled for this opportunity.")
    return opportunity


@login_required
@org_program_manager_required
def audit_report_list(request, org_slug, opportunity_id):
    opportunity = _require_flagged_opportunity(org_slug, opportunity_id)
    # Annotate each row with its chronological position
    queryset = (
        AuditReport.objects.filter(opportunity=opportunity)
        .select_related("completed_by")
        .annotate(serial=Window(expression=RowNumber(), order_by=F("period_end").asc()))
    )

    total_count = queryset.count()
    pending_count = queryset.filter(status=AuditReport.Status.PENDING).count()
    completed_count = queryset.filter(status=AuditReport.Status.COMPLETED).count()

    table = AuditReportTable(queryset, opportunity=opportunity)
    RequestConfig(request, paginate={"per_page": DEFAULT_PAGE_SIZE}).configure(table)

    path = [
        {"title": "Opportunities", "url": reverse("opportunity:list", args=(org_slug,))},
        {
            "title": opportunity.name,
            "url": reverse("opportunity:detail", args=(org_slug, opportunity.opportunity_id)),
        },
        {"title": "Audits"},
    ]

    return render(
        request,
        "audit/audit_report_list.html",
        {
            "opportunity": opportunity,
            "table": table,
            "total_count": total_count,
            "pending_count": pending_count,
            "completed_count": completed_count,
            "path": path,
        },
    )


def _column_specs(entries):
    """Calculation columns to render, ordered by registry then by appearance."""
    registry_names = [c.name for c in get_registered_calculations()]
    seen = {}
    for entry in entries:
        for name, result in entry.results.items():
            if name not in seen:
                seen[name] = result.get("label", name)
    ordered = [(name, seen[name]) for name in registry_names if name in seen]
    leftovers = [(name, label) for name, label in seen.items() if name not in registry_names]
    return ordered + leftovers


@login_required
@org_program_manager_required
def audit_report_detail(request, org_slug, opportunity_id, audit_report_id):
    opportunity = _require_flagged_opportunity(org_slug, opportunity_id)
    report = get_object_or_404(AuditReport, audit_report_id=audit_report_id, opportunity=opportunity)

    name_filter = request.GET.get("filter", "").strip()
    qs = report.entries.select_related("opportunity_access__user")
    if name_filter:
        qs = qs.filter(opportunity_access__user__name__icontains=name_filter)
    entries = list(qs.order_by("opportunity_access__user__name"))

    all_entries = list(report.entries.all())
    columns_spec = _column_specs(all_entries)

    to_review = [e for e in entries if e.flagged and not e.reviewed]
    no_action = [e for e in entries if not e.flagged or e.reviewed]

    total_flagged = sum(1 for e in all_entries if e.flagged)
    reviewed_count = sum(1 for e in all_entries if e.flagged and e.reviewed)

    review_table = AuditReportEntryTable(
        to_review, opportunity=opportunity, report=report, columns_spec=columns_spec, prefix="review-"
    )
    no_action_table = AuditReportEntryTable(
        no_action, opportunity=opportunity, report=report, columns_spec=columns_spec, prefix="noaction-"
    )
    RequestConfig(request, paginate={"per_page": DEFAULT_PAGE_SIZE}).configure(review_table)
    RequestConfig(request, paginate={"per_page": DEFAULT_PAGE_SIZE}).configure(no_action_table)

    path = [
        {"title": "Opportunities", "url": reverse("opportunity:list", args=(org_slug,))},
        {
            "title": opportunity.name,
            "url": reverse("opportunity:detail", args=(org_slug, opportunity.opportunity_id)),
        },
        {
            "title": "Audits",
            "url": reverse(
                "opportunity:audit:audit_report_list",
                kwargs={"org_slug": org_slug, "opportunity_id": opportunity.opportunity_id},
            ),
        },
        {"title": f"{report.period_start} – {report.period_end}"},
    ]

    context = {
        "opportunity": opportunity,
        "report": report,
        "review_table": review_table,
        "no_action_table": no_action_table,
        "reviewed_count": reviewed_count,
        "total_flagged": total_flagged,
        "can_complete": total_flagged == reviewed_count and report.status == AuditReport.Status.PENDING,
        "name_filter": name_filter,
        "path": path,
    }

    template = (
        "audit/_audit_report_body.html"
        if request.headers.get("HX-Request") == "true"
        else "audit/audit_report_detail.html"
    )
    return render(request, template, context)
