from datetime import timedelta

from django.db import transaction
from django.http import Http404, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST
from django_tables2 import RequestConfig

from commcare_connect.audit.calculations import get_registered_calculations
from commcare_connect.audit.models import AuditReport, AuditReportEntry
from commcare_connect.audit.tables import AuditReportEntryTable, AuditReportTable
from commcare_connect.flags.flag_names import WEEKLY_PERFORMANCE_REPORT
from commcare_connect.flags.models import Flag
from commcare_connect.opportunity.exceptions import TaskAlreadyAssignedError
from commcare_connect.opportunity.models import AssignedTask, TaskType
from commcare_connect.organization.decorators import opportunity_required, org_program_manager_required

DEFAULT_PAGE_SIZE = 25
DEFAULT_TASK_DUE_DAYS = 7


def _require_feature_flag(opportunity):
    try:
        flag = Flag.objects.get(name=WEEKLY_PERFORMANCE_REPORT)
    except Flag.DoesNotExist:
        raise Http404("Weekly performance report is not enabled.")
    if not flag.opportunities.filter(pk=opportunity.pk).exists():
        raise Http404("Weekly performance report is not enabled for this opportunity.")


@org_program_manager_required
@opportunity_required
def audit_report_list(request, org_slug, opp_id):
    opportunity = request.opportunity
    _require_feature_flag(opportunity)
    queryset = AuditReport.objects.filter(opportunity=opportunity).select_related("completed_by")

    total_count = queryset.count()
    pending_count = queryset.filter(status=AuditReport.Status.PENDING).count()
    completed_count = queryset.filter(status=AuditReport.Status.COMPLETED).count()

    table = AuditReportTable(queryset, opportunity=opportunity, org_slug=org_slug)
    RequestConfig(request, paginate={"per_page": DEFAULT_PAGE_SIZE}).configure(table)

    path = [
        {"title": _("Opportunities"), "url": reverse("opportunity:list", args=(org_slug,))},
        {
            "title": opportunity.name,
            "url": reverse("opportunity:detail", args=(org_slug, opportunity.opportunity_id)),
        },
        {"title": _("Audits")},
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


@opportunity_required
@org_program_manager_required
def audit_report_detail(request, org_slug, opp_id, audit_report_id):
    opportunity = request.opportunity
    _require_feature_flag(opportunity)
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
        to_review,
        opportunity=opportunity,
        report=report,
        columns_spec=columns_spec,
        prefix="review-",
        org_slug=org_slug,
    )
    no_action_table = AuditReportEntryTable(
        no_action,
        opportunity=opportunity,
        report=report,
        columns_spec=columns_spec,
        prefix="noaction-",
        org_slug=org_slug,
    )
    RequestConfig(request, paginate={"per_page": DEFAULT_PAGE_SIZE}).configure(review_table)
    RequestConfig(request, paginate={"per_page": DEFAULT_PAGE_SIZE}).configure(no_action_table)

    path = [
        {"title": _("Opportunities"), "url": reverse("opportunity:list", args=(org_slug,))},
        {
            "title": opportunity.name,
            "url": reverse("opportunity:detail", args=(org_slug, opportunity.opportunity_id)),
        },
        {
            "title": _("Audits"),
            "url": reverse(
                "opportunity:audit:audit_report_list",
                kwargs={"org_slug": org_slug, "opp_id": opportunity.opportunity_id},
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
        "org_slug": org_slug,
    }

    template = (
        "audit/audit_report_body.html"
        if request.headers.get("HX-Request") == "true"
        else "audit/audit_report_detail.html"
    )
    return render(request, template, context)


@opportunity_required
@org_program_manager_required
def audit_report_task_modal(request, org_slug, opp_id, audit_report_id, entry_id):
    opportunity = request.opportunity
    _require_feature_flag(opportunity)
    report = get_object_or_404(AuditReport, audit_report_id=audit_report_id, opportunity=opportunity)
    entry = get_object_or_404(AuditReportEntry, audit_report_entry_id=entry_id, audit_report=report)

    failed = [(name, r) for name, r in entry.results.items() if r.get("has_sufficient_data") and not r.get("in_range")]
    task_types = TaskType.objects.filter(opportunity=opportunity).order_by("name")

    return render(
        request,
        "audit/audit_report_task_modal.html",
        {
            "opportunity": opportunity,
            "report": report,
            "entry": entry,
            "failed": failed,
            "task_types": task_types,
            "org_slug": org_slug,
        },
    )


@opportunity_required
@org_program_manager_required
@require_POST
def audit_report_task_action(request, org_slug, opp_id, audit_report_id, entry_id):
    opportunity = request.opportunity
    _require_feature_flag(opportunity)
    report = get_object_or_404(AuditReport, audit_report_id=audit_report_id, opportunity=opportunity)
    entry = get_object_or_404(AuditReportEntry, audit_report_entry_id=entry_id, audit_report=report)

    if report.status == AuditReport.Status.COMPLETED:
        return HttpResponseBadRequest("Report is already completed.")
    if entry.reviewed:
        return HttpResponseBadRequest("Entry has already been reviewed.")

    action = request.POST.get("action")
    if action == "tasks_assigned":
        task_type_ids = request.POST.getlist("task_type_ids")
        task_types = TaskType.objects.filter(pk__in=task_type_ids, opportunity=opportunity)
        due_date = timezone.now().date() + timedelta(days=DEFAULT_TASK_DUE_DAYS)
        try:
            with transaction.atomic():
                for task_type in task_types:
                    AssignedTask.assign(
                        task_type=task_type,
                        opportunity_access=entry.opportunity_access,
                        due_date=due_date,
                        assigned_by=request.user,
                    )
        except TaskAlreadyAssignedError:
            return HttpResponseBadRequest(
                _("Task assignment not completed: '%(name)s' is already assigned to this worker.")
                % {"name": task_type.name}
            )
        entry.review_action = AuditReportEntry.ReviewAction.TASKS_ASSIGNED
    elif action == "none":
        entry.review_action = AuditReportEntry.ReviewAction.NONE
    else:
        return HttpResponseBadRequest(_("Unknown action."))

    entry.reviewed = True
    entry.save(update_fields=["reviewed", "review_action", "date_modified"])

    response = HttpResponse(status=200)
    response["HX-Trigger"] = "refreshDetail"
    return response


@opportunity_required
@org_program_manager_required
@require_POST
def audit_report_complete(request, org_slug, opp_id, audit_report_id):
    opportunity = request.opportunity
    _require_feature_flag(opportunity)
    report = get_object_or_404(AuditReport, audit_report_id=audit_report_id, opportunity=opportunity)

    unreviewed_flagged = report.entries.filter(flagged=True, reviewed=False).exists()
    if unreviewed_flagged:
        return HttpResponseBadRequest("All flagged entries must be reviewed before completing the audit.")

    if report.status != AuditReport.Status.COMPLETED:
        report.status = AuditReport.Status.COMPLETED
        report.completed_by = request.user
        report.completed_date = timezone.now()
        report.save(update_fields=["status", "completed_by", "completed_date", "date_modified"])

    return HttpResponse(status=204)
