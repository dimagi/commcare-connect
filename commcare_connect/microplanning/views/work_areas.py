import json
import logging

import pghistory
from django.contrib import messages
from django.db import transaction
from django.db.models import F
from django.http import HttpResponse, JsonResponse, StreamingHttpResponse
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_POST
from django.views.generic.edit import UpdateView
from waffle.decorators import waffle_flag

from commcare_connect.commcarehq.api import bulk_create_or_update_cases, create_or_update_case_by_work_area
from commcare_connect.flags.flag_names import MICROPLANNING
from commcare_connect.microplanning.const import (
    HQ_BULK_CHUNK_SIZE,
    MAX_EXCLUDE_WORK_AREAS,
)
from commcare_connect.microplanning.filters import WorkAreaMapFilterSet
from commcare_connect.microplanning.forms import WorkAreaModelForm
from commcare_connect.microplanning.models import (
    WorkArea,
    WorkAreaGroup,
    WorkAreaStatus,
)
from commcare_connect.microplanning.tasks import WorkAreaCSVExporter
from commcare_connect.opportunity.models import UserVisit, VisitValidationStatus
from commcare_connect.organization.decorators import (
    opp_admin_access_required,
    opportunity_required,
)
from commcare_connect.utils.commcarehq_api import CommCareHQAPIException

logger = logging.getLogger(__name__)


@require_POST
@opp_admin_access_required
@opportunity_required
def exclude_work_areas(request, org_slug, opp_id):
    exclusion_reason = request.POST.get("exclusion_reason", "").strip()
    if not exclusion_reason:
        return JsonResponse({"error": _("Exclusion reason is required")}, status=400)
    if len(exclusion_reason) > 500:
        return JsonResponse({"error": _("Exclusion reason must be at most 500 characters")}, status=400)

    raw_ids = request.POST.getlist("work_area_ids[]")
    if not raw_ids:
        return JsonResponse({"error": _("Work Area IDs is required")}, status=400)
    if len(raw_ids) > MAX_EXCLUDE_WORK_AREAS:
        return JsonResponse(
            {"error": _("Work Area IDs must contain at most %(max)d items") % {"max": MAX_EXCLUDE_WORK_AREAS}},
            status=400,
        )

    try:
        work_area_ids = [int(i) for i in raw_ids]
    except (ValueError, TypeError):
        return JsonResponse({"error": _("Work Area IDs must be integers")}, status=400)

    result = _exclude_work_areas_for_opportunity(
        opportunity=request.opportunity,
        work_area_ids=work_area_ids,
        user=request.user,
        exclusion_reason=exclusion_reason,
    )
    response = HttpResponse('<div id="exclude-progress"></div>')
    response.headers["HX-Trigger"] = json.dumps(
        {"work_areas_excluded": {"excluded": result["excluded_ids"], "skipped": result["skipped"]}}
    )
    return response


def _exclude_work_areas_for_opportunity(opportunity, work_area_ids, user, exclusion_reason):
    """Exclude work areas and unassign their HQ cases.
    HQ calls are batched by HQ_BULK_CHUNK_SIZE; a batch failure skips DB exclusion
    for the whole chunk.
    """
    excluded_ids = []
    skipped = 0
    failed = 0

    work_areas_map = {wa.id: wa for wa in WorkArea.objects.filter(id__in=work_area_ids, opportunity=opportunity)}

    wa_to_wag_id_map = {wa.id: wa.work_area_group_id for wa in work_areas_map.values()}

    api_key = opportunity.api_key
    domain = opportunity.deliver_app.cc_domain if opportunity.deliver_app else None

    needs_hq = []
    db_only = []

    for work_area_id in work_area_ids:
        work_area = work_areas_map.get(work_area_id)
        if work_area is None or work_area.status not in (WorkAreaStatus.NOT_VISITED, WorkAreaStatus.UNASSIGNED):
            skipped += 1
            continue

        if work_area.case_id and api_key and domain:
            needs_hq.append(work_area)
        else:
            db_only.append(work_area)

    pghistory_ctx = dict(reason=exclusion_reason, username=user.username, user_email=user.email)

    for i in range(0, len(needs_hq), HQ_BULK_CHUNK_SIZE):
        chunk = needs_hq[i : i + HQ_BULK_CHUNK_SIZE]
        # HQ's "unassigned" convention is "-"; empty string falls back to the submitting user.
        updates = [{"case_id": str(wa.case_id), "owner_id": "-"} for wa in chunk]
        try:
            with transaction.atomic(), pghistory.context(**pghistory_ctx):
                _bulk_exclude(chunk, user, exclusion_reason)
                bulk_create_or_update_cases(api_key, domain, updates)
        except CommCareHQAPIException as e:
            logger.warning("Failed to unassign HQ case chunk (size=%d): %s", len(chunk), e)
            failed += len(chunk)
            continue
        excluded_ids.extend(wa.id for wa in chunk)

    if db_only:
        with transaction.atomic(), pghistory.context(**pghistory_ctx):
            _bulk_exclude(db_only, user, exclusion_reason)
        excluded_ids.extend(wa.id for wa in db_only)

    work_area_group_ids = {wa_to_wag_id_map[wa_id] for wa_id in excluded_ids}
    work_area_groups = list(WorkAreaGroup.objects.filter(id__in=work_area_group_ids))
    for wag in work_area_groups:
        wag.update_centroid(commit=False)
    WorkAreaGroup.objects.bulk_update(work_area_groups, fields=["centroid"])

    logger.info(
        "exclude_work_areas_for_opportunity finished opp=%s requested=%d excluded=%d skipped=%d failed=%d",
        opportunity.id,
        len(work_area_ids),
        len(excluded_ids),
        skipped,
        failed,
    )
    return {"excluded_ids": excluded_ids, "skipped": skipped, "failed": failed}


def _bulk_exclude(work_areas, user, exclusion_reason):
    for wa in work_areas:
        wa.status = WorkAreaStatus.EXCLUDED
        wa.excluded_by = user
        wa.excluded_reason = exclusion_reason
        wa.work_area_group = None
    WorkArea.objects.bulk_update(
        work_areas,
        fields=["status", "excluded_by", "excluded_reason", "work_area_group"],
    )


@require_POST
@opp_admin_access_required
@opportunity_required
@waffle_flag(MICROPLANNING)
def clear_work_areas(request, org_slug, opp_id):
    redirect_url = reverse("microplanning:microplanning_home", kwargs={"org_slug": org_slug, "opp_id": opp_id})
    work_areas = WorkArea.objects.filter(opportunity_id=request.opportunity.id)

    access_ids = list(work_areas.select_for_update().values_list("opportunity_access_id", flat=True))
    if any(access_id is not None for access_id in access_ids):
        messages.error(request, _("Work Areas cannot be cleared as they are assigned to users."))
        return HttpResponse(headers={"HX-Redirect": redirect_url})

    if work_areas.filter(uservisit__isnull=False).exists():
        messages.error(request, _("Visits have been recorded against these Work Areas. They cannot be cleared."))
        return HttpResponse(headers={"HX-Redirect": redirect_url})

    work_areas.delete()
    WorkAreaGroup.objects.filter(opportunity_id=request.opportunity.id).delete()

    messages.success(request, _("Work Areas and Work Area Groups cleared. You can now upload a new file."))
    return HttpResponse(headers={"HX-Redirect": redirect_url})


@require_GET
@opp_admin_access_required
@opportunity_required
@waffle_flag(MICROPLANNING)
def download_work_areas(request, org_slug, opp_id):
    opportunity = request.opportunity
    base_qs = WorkArea.objects.filter(opportunity=opportunity).exclude(status=WorkAreaStatus.EXCLUDED)
    filterset = WorkAreaMapFilterSet(request.GET, queryset=base_qs, opportunity=opportunity)
    queryset = filterset.qs.annotate(group_name=F("work_area_group__name"))
    response = StreamingHttpResponse(WorkAreaCSVExporter.rows(queryset), content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="work_area_summary_{opportunity.opportunity_id}.csv"'
    return response


@method_decorator([opp_admin_access_required, opportunity_required, waffle_flag(MICROPLANNING)], name="dispatch")
class ModifyWorkAreaUpdateView(UpdateView):
    model = WorkArea
    form_class = WorkAreaModelForm
    template_name = "microplanning/work_area_form.html"
    pk_url_kwarg = "work_area_id"
    context_object_name = "work_area"

    def get_queryset(self):
        return super().get_queryset().filter(opportunity=self.request.opportunity)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["opportunity"] = self.request.opportunity
        return kwargs

    def form_valid(self, form):
        work_area = form.save(commit=False)
        reason = form.cleaned_data.pop("reason", "")
        old_wag_id = form.initial.get("work_area_group")
        updated_wag = work_area.work_area_group
        updated_wag_id = getattr(updated_wag, "id", None)
        try:
            with transaction.atomic(), pghistory.context(reason=reason):
                work_area.save(update_fields=["expected_visit_count", "work_area_group"])
                if "expected_visit_count" in form.changed_data:
                    work_area.update_status()

                if updated_wag_id != old_wag_id and updated_wag:
                    updated_wag.update_centroid()

                if form.has_changed() and work_area.opportunity_access_id:
                    # let exception bubble up if case update fails, to avoid saving work area without case sync
                    create_or_update_case_by_work_area(work_area)
        except CommCareHQAPIException as e:
            logger.info(f"Failed to update case for work area {work_area.id} after form submission. Error: {e}")
            form.add_error(
                None,
                _("Failed to update the work area. Please try again, and if the issue persists, contact support."),
            )
            return super().form_invalid(form)

        visits_completed = UserVisit.objects.filter(
            opportunity=work_area.opportunity,
            work_area=work_area,
            status=VisitValidationStatus.approved,
        ).count()
        response = HttpResponse(status=204)
        response["HX-Trigger"] = json.dumps(
            {
                "workAreaUpdated": {
                    "id": work_area.id,
                    "expected_visit_count": work_area.expected_visit_count,
                    "group_id": work_area.work_area_group_id,
                    "group_name": getattr(work_area.work_area_group, "name", None),
                    "slug": work_area.slug,
                    "visits_completed": visits_completed,
                }
            }
        )

        if updated_wag_id != old_wag_id and old_wag_id:
            old_wag = WorkAreaGroup.objects.get(id=old_wag_id)
            old_wag.update_centroid()

        return response
