from django.conf import settings
from django.core.cache import cache
from django.db.models import (
    Count,
    F,
    Q,
    Sum,
)
from django.shortcuts import render
from django.urls import reverse
from django.utils import formats, timezone
from django.utils.translation import gettext, ngettext
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET
from waffle.decorators import waffle_flag

from commcare_connect.flags.flag_names import MICROPLANNING
from commcare_connect.microplanning.buildings import buildings_overlay_config
from commcare_connect.microplanning.const import (
    INACCESSIBILITY_LEGEND,
    MAX_AUTOZOOM_ZOOM,
    WORK_AREA_STATUS_COLORS,
    WORKAREA_MIN_ZOOM,
)
from commcare_connect.microplanning.coverage_progress import (
    IN_SCOPE_WORK_AREA,
    annotate_approved_visit_counts,
)
from commcare_connect.microplanning.filters import WorkAreaMapFilterSet
from commcare_connect.microplanning.forms import AssignmentModeForm, ClusterWorkAreasForm
from commcare_connect.microplanning.models import (
    ImplementationArea,
    InaccessibilityRequestStatus,
    WorkArea,
    WorkAreaGroup,
    WorkAreaInaccessibilityRequest,
    WorkAreaStatus,
)
from commcare_connect.microplanning.tasks import (
    get_implementation_area_import_cache_key,
    get_import_area_cache_key,
)
from commcare_connect.microplanning.utils import quoted_missing_deliver_units
from commcare_connect.opportunity.models import BlobMeta, OpportunityAccess
from commcare_connect.organization.decorators import (
    opp_admin_access_required,
    opportunity_required,
)
from commcare_connect.utils.percentages import pct


@require_GET
@opp_admin_access_required
@opportunity_required
@waffle_flag(MICROPLANNING)
def microplanning_home(request, *args, **kwargs):
    opportunity = request.opportunity
    work_area_count = WorkArea.objects.filter(opportunity_id=opportunity.id).count()
    work_area_group_count = WorkAreaGroup.objects.filter(opportunity_id=opportunity.id).count()
    implementation_area_count = ImplementationArea.objects.filter(opportunity_id=opportunity.id).count()
    areas_assigned = WorkArea.objects.filter(opportunity_id=opportunity.id, opportunity_access__isnull=False).exists()

    areas_present = bool(work_area_count)
    work_area_groups_present = bool(work_area_group_count)
    implementation_areas_present = bool(implementation_area_count)

    show_area_btn = cache.get(get_import_area_cache_key(opportunity.id)) is None
    show_workarea_groups_btn = areas_present and not work_area_groups_present
    show_clear_work_areas_btn = areas_present and not areas_assigned
    show_rerun_clear_work_area_groups_btn = areas_present and not areas_assigned and work_area_groups_present
    show_implementation_area_btn = not (
        cache.get(get_implementation_area_import_cache_key(opportunity.id)) is not None or implementation_areas_present
    )
    clear_data_details = _get_clear_data_details(
        work_area_count=work_area_count,
        work_area_group_count=work_area_group_count,
        implementation_area_count=implementation_area_count,
        areas_assigned=areas_assigned,
    )

    tiles_url = reverse(
        "microplanning:workareas_tiles",
        kwargs={"org_slug": request.org.slug, "opp_id": opportunity.opportunity_id, "z": 0, "x": 0, "y": 0},
    ).replace("/0/0/0", "/{z}/{x}/{y}")

    visit_tiles_url = reverse(
        "microplanning:user_visit_tiles",
        kwargs={"org_slug": request.org.slug, "opp_id": opportunity.opportunity_id, "z": 0, "x": 0, "y": 0},
    ).replace("/0/0/0", "/{z}/{x}/{y}")

    groups_url = reverse(
        "microplanning:workareas_group_geojson",
        kwargs={
            "org_slug": request.org.slug,
            "opp_id": opportunity.opportunity_id,
        },
    )

    implementation_areas_url = reverse(
        "microplanning:implementation_areas_geojson",
        kwargs={
            "org_slug": request.org.slug,
            "opp_id": opportunity.opportunity_id,
        },
    )

    edit_work_area_url = reverse(
        "microplanning:modify_work_area",
        args=[request.org.slug, opportunity.opportunity_id, 0],
    ).replace("/0/", "/")

    user_visit_data_url = reverse(
        "opportunity:user_visit_data",
        args=[request.org.slug, opportunity.opportunity_id, 0],
    ).replace("/0/", "/")

    download_url = reverse(
        "microplanning:download_work_areas",
        kwargs={"org_slug": request.org.slug, "opp_id": opportunity.opportunity_id},
    )

    exclude_url = reverse(
        "microplanning:exclude_work_areas",
        kwargs={"org_slug": request.org.slug, "opp_id": opportunity.opportunity_id},
    )

    bounds_url = reverse(
        "microplanning:workareas_bounds",
        kwargs={"org_slug": request.org.slug, "opp_id": opportunity.opportunity_id},
    )

    search_options_url = reverse(
        "microplanning:search_options",
        kwargs={"org_slug": request.org.slug, "opp_id": opportunity.opportunity_id},
    )

    work_area_detail_url = reverse(
        "microplanning:work_area_detail",
        args=[request.org.slug, opportunity.opportunity_id, 0],
    ).replace("/0/", "/")

    # The review sidebar builds photo thumbnail URLs by appending a blob id to this.
    attachment_url_base = reverse(
        "opportunity:fetch_attachment",
        args=[request.org.slug, opportunity.opportunity_id, "BLOB_ID"],
    ).replace("BLOB_ID", "")

    status_meta = {
        status.value: {
            "label": status.label,
            "class": WORK_AREA_STATUS_COLORS.get(status),
        }
        for status in WorkAreaStatus
    }

    # After an upload the view redirects here with ?task_id=&area_type=; the auto-poll must
    # hit the status endpoint matching the area that was uploaded so the modal shows the right labels.
    area_type = request.GET.get("area_type", "work_area")
    status_url_name = (
        "microplanning:implementation_area_import_status"
        if area_type == "implementation_area"
        else "microplanning:import_status"
    )
    import_status_url = reverse(
        status_url_name,
        kwargs={"org_slug": request.org.slug, "opp_id": opportunity.opportunity_id},
    )

    assignment_mode = bool(request.GET.get("assignment_mode"))
    inaccessible_mode = bool(request.GET.get("inaccessible_mode"))
    # Drives both the entry button's count and the list Inaccessible Mode renders, so the two
    # can never disagree.
    inaccessibility_requests = _pending_inaccessibility_requests(opportunity)

    filterset = WorkAreaMapFilterSet(
        data=request.GET,
        opportunity=opportunity,
    )

    context = {
        "show_area_btn": show_area_btn,
        "work_areas_present": areas_present,
        "show_implementation_area_btn": show_implementation_area_btn,
        "implementation_areas_present": implementation_areas_present,
        "show_workarea_groups_btn": show_workarea_groups_btn,
        "show_clear_work_areas_btn": show_clear_work_areas_btn,
        "clear_data_details": clear_data_details,
        "show_rerun_clear_work_area_groups_btn": show_rerun_clear_work_area_groups_btn,
        "clustering_is_rerun": show_rerun_clear_work_area_groups_btn,
        "mapbox_api_key": settings.MAPBOX_TOKEN,
        "buildings_config": buildings_overlay_config(),
        "task_id": request.GET.get("task_id"),
        "import_status_url": import_status_url,
        "opportunity": opportunity,
        "path": [
            {"title": _("Opportunities"), "url": reverse("opportunity:list", kwargs={"org_slug": request.org.slug})},
            {
                "title": opportunity.name,
                "url": reverse("opportunity:detail", args=(request.org.slug, opportunity.opportunity_id)),
            },
            {"title": _("Progress Map")},
        ],
        "metrics": _get_metrics_for_microplanning(opportunity),
        "tiles_url": tiles_url,
        "visit_tiles_url": visit_tiles_url,
        "groups_url": groups_url,
        "implementation_areas_url": implementation_areas_url,
        "status_meta": status_meta,
        "workarea_min_zoom": WORKAREA_MIN_ZOOM,
        "max_autozoom_zoom": MAX_AUTOZOOM_ZOOM,
        "edit_work_area_url": edit_work_area_url,
        "user_visit_data_url": user_visit_data_url,
        "download_url": download_url,
        "review_inaccessibility_url": reverse(
            "microplanning:review_inaccessibility_request",
            args=[request.org.slug, opportunity.opportunity_id, 0],
        ).replace("/0/", "/"),
        "exclude_url": exclude_url,
        "filter_form": filterset.form,
        "cluster_form": ClusterWorkAreasForm(),
        "assignment_mode": assignment_mode,
        "inaccessible_mode": inaccessible_mode,
        "inaccessibility_requests": inaccessibility_requests,
        "inaccessible_request_count": len(inaccessibility_requests),
        "attachment_url_base": attachment_url_base,
        "inaccessibility_legend": INACCESSIBILITY_LEGEND,
        "denied_inaccessibility_work_area_ids": (
            _denied_inaccessibility_work_area_ids(opportunity) if inaccessible_mode else []
        ),
        "quoted_missing_deliver_units": quoted_missing_deliver_units(opportunity),
        "bounds_url": bounds_url,
        "search_options_url": search_options_url,
        "work_area_detail_url": work_area_detail_url,
    }

    if assignment_mode:
        context.update(_get_assignment_mode_context(request, opportunity))

    return render(
        request,
        template_name="microplanning/home.html",
        context=context,
    )


def _get_clear_data_details(*, work_area_count, work_area_group_count, implementation_area_count, areas_assigned):
    """Helper text for each entry in the Clear Data dropdown.

    Every Work Area action is also blocked while Work Areas are assigned, so a disabled entry has
    to name the reason that actually applies instead of assuming nothing was uploaded.
    """
    blocked_message = _get_work_areas_blocked_message(work_area_count, areas_assigned)

    if blocked_message:
        work_areas_message = blocked_message
    else:
        work_areas_message = ngettext(
            "%(count)d record — also clears Work Area Groups",
            "%(count)d records — also clears Work Area Groups",
            work_area_count,
        ) % {"count": work_area_count}

    if blocked_message:
        work_area_groups_message = blocked_message
    elif not work_area_group_count:
        work_area_groups_message = _("Clustering has not been run yet.")
    else:
        work_area_groups_message = ngettext("%(count)d group", "%(count)d groups", work_area_group_count) % {
            "count": work_area_group_count
        }

    if not implementation_area_count:
        implementation_areas_message = _("No Implementation Areas have been uploaded yet.")
    else:
        implementation_areas_message = ngettext("%(count)d record", "%(count)d records", implementation_area_count) % {
            "count": implementation_area_count
        }

    return {
        "work_areas": work_areas_message,
        "work_area_groups": work_area_groups_message,
        "implementation_areas": implementation_areas_message,
    }


def _get_work_areas_blocked_message(work_area_count, areas_assigned):
    """Reason Work Area data cannot be cleared, or None when it can be."""
    if not work_area_count:
        return _("No Work Areas have been uploaded yet.")
    if areas_assigned:
        return _("Work Areas are assigned to FLWs and cannot be cleared.")
    return None


def _get_metrics_for_microplanning(opportunity):
    qs = annotate_approved_visit_counts(WorkArea.objects.filter(opportunity=opportunity), opportunity, ncwa=True)

    # Work Areas Done counts an area still awaiting review, since raising the request is as far
    # as the worker can take it. The Inaccessible Work Areas tile counts only the areas whose
    # inaccessibility request was accepted.
    inaccessible_or_requested = Q(status__in=(WorkAreaStatus.INACCESSIBLE, WorkAreaStatus.REQUEST_FOR_INACCESSIBLE))
    has_evc_target = Q(expected_visit_count__gt=0)
    visited_children_found = Q(hsd_count__gte=1)
    visited_no_children_found = Q(ncwa_count__gte=1)

    # Every tile but Excluded Work Areas is scoped to the in-scope areas.
    agg = qs.aggregate(
        excluded=Count("id", filter=Q(status=WorkAreaStatus.EXCLUDED)),
        in_scope=Count("id", filter=IN_SCOPE_WORK_AREA),
        done=Count(
            "id",
            # An OR filter, so an area meeting several conditions (e.g. inaccessible with an
            # approved visit already on file) is still counted once.
            filter=IN_SCOPE_WORK_AREA
            & (visited_children_found | visited_no_children_found | inaccessible_or_requested),
        ),
        unvisited=Count("id", filter=IN_SCOPE_WORK_AREA & Q(status=WorkAreaStatus.NOT_VISITED)),
        visited_children_found=Count("id", filter=IN_SCOPE_WORK_AREA & visited_children_found),
        visited_no_children_found=Count("id", filter=IN_SCOPE_WORK_AREA & visited_no_children_found),
        evc_reached=Count(
            "id",
            # Ignore areas with no target; 0 >= 0 would otherwise mark them as delivered.
            filter=IN_SCOPE_WORK_AREA & has_evc_target & Q(hsd_count__gte=F("expected_visit_count")),
        ),
        inaccessible=Count("id", filter=IN_SCOPE_WORK_AREA & Q(status=WorkAreaStatus.INACCESSIBLE)),
        total_expected_visits=Sum("expected_visit_count", filter=IN_SCOPE_WORK_AREA),
        total_hsd_visits=Sum("hsd_count", filter=IN_SCOPE_WORK_AREA),
    )

    in_scope_count = agg["in_scope"] or 0

    total_expected = agg["total_expected_visits"] or 0
    if in_scope_count and total_expected:
        total_hsd_visits = agg["total_hsd_visits"] or 0
        pct_wa_visited = (agg["visited_children_found"] or 0) / in_scope_count
        pct_visits = total_hsd_visits / total_expected
        visited_to_visits = round(pct_wa_visited / pct_visits, 2) if pct_visits else "--"
    else:
        visited_to_visits = "--"

    return [
        {
            "name": _("Work Areas Done"),
            "value": agg["done"],
            "percentage": pct(agg["done"], in_scope_count, ndigits=None),
        },
        {
            "name": _("Unvisited Work Areas"),
            "value": agg["unvisited"],
            "percentage": pct(agg["unvisited"], in_scope_count, ndigits=None),
        },
        {
            "name": _("Visited Work Areas (children found)"),
            "value": agg["visited_children_found"],
            "percentage": pct(agg["visited_children_found"], in_scope_count, ndigits=None),
        },
        {
            "name": _("Visited Work Areas (no children found)"),
            "value": agg["visited_no_children_found"],
            "percentage": pct(agg["visited_no_children_found"], in_scope_count, ndigits=None),
        },
        {
            "name": _("EVC Reached"),
            "value": agg["evc_reached"],
            "percentage": pct(agg["evc_reached"], in_scope_count, ndigits=None),
        },
        {
            "name": _("Inaccessible Work Areas"),
            "value": agg["inaccessible"],
            "percentage": pct(agg["inaccessible"], in_scope_count, ndigits=None),
        },
        {"name": _("Excluded Work Areas"), "value": agg["excluded"]},
        {"name": _("WA Visited : Visits Ratio"), "value": visited_to_visits},
    ]


def _get_assignment_mode_context(request, opportunity):
    org_slug = request.org.slug
    opp_id = opportunity.opportunity_id
    return {
        "assignment_form": AssignmentModeForm(opportunity=opportunity),
        "assignees_json": list(
            OpportunityAccess.objects.filter(opportunity=opportunity, accepted=True, suspended=False)
            .select_related("user")
            .values("id", "user__name", "user__username", "user__user_id")
        ),
        "group_work_areas_url": reverse(
            "microplanning:get_work_areas_for_assignment",
            args=[org_slug, opp_id],
        ),
        "flw_work_areas_url": reverse(
            "microplanning:get_flw_work_areas_for_assignment",
            args=[org_slug, opp_id, 0],
        ).replace("/0/", "/__assignee_id__/"),
        "flw_summary_url": reverse(
            "microplanning:get_flw_summary_for_assignment",
            kwargs={"org_slug": org_slug, "opp_id": opp_id},
        ),
        "assignment_save_url": reverse(
            "microplanning:save_assignment",
            kwargs={"org_slug": org_slug, "opp_id": opp_id},
        ),
        "assignment_unassign_url": reverse(
            "microplanning:unassign_work_areas",
            kwargs={"org_slug": org_slug, "opp_id": opp_id},
        ),
        "user_visits_url": reverse(
            "opportunity:user_visits_list",
            args=[org_slug, opp_id],
        ),
        "worker_list_url": reverse(
            "opportunity:worker_work_areas",
            args=[org_slug, opp_id],
        ),
    }


def _pending_inaccessibility_requests(opportunity):
    """Work areas awaiting an inaccessibility review, longest outstanding first.

    The review sidebar renders these rows from JSON rather than through template filters, so the
    date and the pending label arrive display-ready. "Pending" counts from the date of visit the
    worker reported, the only date the request carries.
    """
    today = timezone.localdate()
    pending_requests = (
        WorkAreaInaccessibilityRequest.objects.filter(
            status=InaccessibilityRequestStatus.PENDING,
            work_area__opportunity=opportunity,
            work_area__status=WorkAreaStatus.REQUEST_FOR_INACCESSIBLE,
        )
        .order_by("date_of_visit")
        .values(
            "work_area_id",
            "xform_id",
            "date_of_visit",
            "work_area__slug",
            "work_area__work_area_group__name",
            "work_area__implementation_area_name",
            "opportunity_access__user__name",
        )
    )
    photo_blob_ids = _inaccessibility_photo_blob_ids([request["xform_id"] for request in pending_requests])
    return [
        {
            "work_area_id": pending_request["work_area_id"],
            "slug": pending_request["work_area__slug"],
            "group_name": pending_request["work_area__work_area_group__name"],
            "implementation_area_name": pending_request["work_area__implementation_area_name"],
            "flw_name": pending_request["opportunity_access__user__name"],
            "date_of_visit": formats.date_format(pending_request["date_of_visit"]),
            "pending_label": gettext("{days}d pending").format(days=(today - pending_request["date_of_visit"]).days),
            "photo_blob_id": photo_blob_ids.get(pending_request["xform_id"]),
        }
        for pending_request in pending_requests
    ]


def _denied_inaccessibility_work_area_ids(opportunity):
    """Work areas whose inaccessibility request was denied and not since re-raised.

    A denial returns the area to Not Visited, which the tiles cannot tell apart from an area nobody
    ever raised a request on — so the key's "Denied" colour has to be driven by these ids.
    """
    return list(
        WorkAreaInaccessibilityRequest.objects.filter(
            status=InaccessibilityRequestStatus.DENIED,
            work_area__opportunity=opportunity,
        )
        .exclude(work_area__status__in=[WorkAreaStatus.REQUEST_FOR_INACCESSIBLE, WorkAreaStatus.INACCESSIBLE])
        .values_list("work_area_id", flat=True)
        .distinct()
    )


def _inaccessibility_photo_blob_ids(xform_ids):
    """One blob id per form that submitted a photo, for the list's thumbnails.

    A form can carry more than one attachment; the sidebar shows a single thumbnail, so the first
    blob found for a form wins.
    """
    blob_ids = {}
    for parent_id, blob_id in BlobMeta.objects.filter(parent_id__in=xform_ids).values_list("parent_id", "blob_id"):
        blob_ids.setdefault(parent_id, blob_id)
    return blob_ids
