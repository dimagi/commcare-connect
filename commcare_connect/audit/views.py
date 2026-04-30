from django.contrib.auth.decorators import login_required
from django.db.models import F, Window
from django.db.models.functions import RowNumber
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django_tables2 import RequestConfig

from commcare_connect.audit.models import AuditReport
from commcare_connect.audit.tables import AuditReportTable
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


def audit_report_detail(request, org_slug, opportunity_id, audit_report_id):
    # Stub — real implementation in Task 11.
    raise Http404()
