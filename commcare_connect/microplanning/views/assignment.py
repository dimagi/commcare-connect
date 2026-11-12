import json

from django.db import transaction
from django.db.models import (
    Count,
    F,
    Sum,
)
from django.http import JsonResponse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_POST
from waffle.decorators import waffle_flag

from commcare_connect.flags.flag_names import MICROPLANNING
from commcare_connect.microplanning.const import MAX_UNASSIGN_WORK_AREAS
from commcare_connect.microplanning.models import (
    WorkArea,
    WorkAreaStatus,
)
from commcare_connect.microplanning.services import (
    assign_work_areas_and_sync_to_hq,
    unassign_work_areas_for_opportunity,
)
from commcare_connect.microplanning.tasks import send_work_area_assignment_notification
from commcare_connect.microplanning.utils import work_area_bounds
from commcare_connect.opportunity.models import OpportunityAccess
from commcare_connect.organization.decorators import (
    opp_admin_access_required,
    opportunity_required,
)


@require_GET
@opp_admin_access_required
@opportunity_required
@waffle_flag(MICROPLANNING)
def get_work_areas_for_assignment(request, org_slug, opp_id):
    group_ids = request.GET.getlist("group_id")
    queryset = WorkArea.objects.filter(
        opportunity=request.opportunity,
        work_area_group_id__in=group_ids,
    )
    work_areas = list(
        queryset.values("id", "building_count", "expected_visit_count", "status", group_id=F("work_area_group_id"))
    )
    # Assignment mode selects these client-side rather than through the tile filters, so the map
    # can't get their extent from workareas_bounds; it rides along here to drive the auto-zoom.
    return JsonResponse({"work_areas": work_areas, "bounds": work_area_bounds(queryset)})


@require_GET
@opp_admin_access_required
@opportunity_required
@waffle_flag(MICROPLANNING)
def get_flw_work_areas_for_assignment(request, org_slug, opp_id, assignee_id):
    queryset = WorkArea.objects.filter(
        opportunity=request.opportunity,
        opportunity_access_id=assignee_id,
    )
    work_areas = list(queryset.values("id", "building_count", "expected_visit_count", "status"))
    return JsonResponse({"work_areas": work_areas, "bounds": work_area_bounds(queryset)})


@require_GET
@opp_admin_access_required
@opportunity_required
@waffle_flag(MICROPLANNING)
def get_flw_summary_for_assignment(request, org_slug, opp_id):
    assignee_id = request.GET.get("assignee_id")
    if not assignee_id:
        return JsonResponse({"error": "assignee_id required"}, status=400)

    stats = WorkArea.objects.filter(
        opportunity=request.opportunity,
        opportunity_access_id=assignee_id,
    ).aggregate(
        buildings=Sum("building_count"),
        visits=Sum("expected_visit_count"),
        work_areas=Count("id"),
    )
    return JsonResponse(
        {
            "assigned_buildings": stats["buildings"] or 0,
            "assigned_visits": stats["visits"] or 0,
            "assigned_work_areas": stats["work_areas"],
        }
    )


@require_POST
@opp_admin_access_required
@opportunity_required
@waffle_flag(MICROPLANNING)
def save_assignment(request, org_slug, opp_id):
    try:
        data = json.loads(request.body)
        assignments = data["assignments"]
        if not assignments:
            raise ValueError
        assignee_ids = {int(entry["assignee_id"]) for entry in assignments}
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return JsonResponse({"error": _("Invalid request body")}, status=400)

    valid_accesses = {
        access.id: access
        for access in OpportunityAccess.objects.filter(
            id__in=assignee_ids,
            opportunity=request.opportunity,
        ).select_related("user")
    }

    invalid_ids = assignee_ids - valid_accesses.keys()
    if invalid_ids:
        return JsonResponse({"error": _("Invalid assignee IDs: %(ids)s") % {"ids": sorted(invalid_ids)}}, status=400)

    try:
        all_wa_ids = [int(wa_id) for entry in assignments for wa_id in entry.get("work_area_ids", [])]
    except (TypeError, ValueError):
        return JsonResponse({"error": _("Work area IDs must be integers")}, status=400)
    requested_wa_ids = set(all_wa_ids)
    if len(all_wa_ids) != len(requested_wa_ids):
        return JsonResponse({"error": _("Duplicate work area IDs in request")}, status=400)

    work_area_to_access = {
        int(wa_id): valid_accesses[int(entry["assignee_id"])]
        for entry in assignments
        for wa_id in entry.get("work_area_ids", [])
    }

    all_work_areas = list(
        WorkArea.objects.filter(
            id__in=requested_wa_ids,
            opportunity=request.opportunity,
        ).select_for_update()
    )

    found_ids = {wa.id for wa in all_work_areas}
    invalid_wa_ids = requested_wa_ids - found_ids
    if invalid_wa_ids:
        return JsonResponse(
            {"error": _("Invalid work area IDs: %(ids)s") % {"ids": sorted(invalid_wa_ids)}}, status=400
        )

    for work_area in all_work_areas:
        work_area.opportunity_access = work_area_to_access[work_area.id]
        if work_area.status == WorkAreaStatus.UNASSIGNED:
            work_area.status = WorkAreaStatus.NOT_VISITED

    result = assign_work_areas_and_sync_to_hq(request.opportunity, all_work_areas, request.user)
    assigned_ids = set(result["assigned_ids"])
    notified_access_ids = {work_area_to_access[wa.id].id for wa in all_work_areas if wa.id in assigned_ids}
    for access_id in notified_access_ids:
        transaction.on_commit(lambda aid=access_id: send_work_area_assignment_notification.delay(aid))

    if result["failed_ids"]:
        return JsonResponse(
            {
                "error": _("Failed to sync %(count)d work area(s) with CommCare HQ. Please try again.")
                % {"count": len(result["failed_ids"])}
            },
            status=502,
        )

    return JsonResponse({"status": "ok"})


@require_POST
@opp_admin_access_required
@opportunity_required
@waffle_flag(MICROPLANNING)
def unassign_work_areas(request, org_slug, opp_id):
    try:
        data = json.loads(request.body)
        raw_ids = data["work_area_ids"]
        if not isinstance(raw_ids, list) or not raw_ids:
            raise ValueError
        # Reject bool/float/str — JSON ints arrive as `int`, anything else is a client bug.
        if any(type(i) is not int for i in raw_ids):
            raise ValueError
        if len(set(raw_ids)) != len(raw_ids):
            raise ValueError
        work_area_ids = raw_ids
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return JsonResponse({"error": _("Invalid request body")}, status=400)

    # Unassignment is a synchronous HQ sync; cap the request size to keep it bounded.
    if len(work_area_ids) > MAX_UNASSIGN_WORK_AREAS:
        return JsonResponse(
            {"error": _("Work Area IDs must contain at most %(max)d items") % {"max": MAX_UNASSIGN_WORK_AREAS}},
            status=400,
        )

    result = unassign_work_areas_for_opportunity(
        opportunity=request.opportunity,
        work_area_ids=work_area_ids,
        user=request.user,
    )

    if result["failed_ids"] and not result["unassigned_ids"]:
        return JsonResponse({"error": _("Failed to sync with CommCare HQ. Please try again.")}, status=502)

    return JsonResponse(
        {
            "status": "ok",
            "unassigned_ids": result["unassigned_ids"],
            "skipped": result["skipped"],
            "failed_ids": result["failed_ids"],
        }
    )
