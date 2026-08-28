import csv
import json
import logging
import uuid
from functools import partial
from http import HTTPStatus

import pghistory
from celery.result import AsyncResult
from crispy_forms.utils import render_crispy_form
from django.conf import settings
from django.contrib import messages
from django.contrib.gis.db.models import Extent, Union
from django.contrib.gis.db.models.fields import PointField
from django.contrib.gis.db.models.functions import AsGeoJSON
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import connection, transaction
from django.db.models import (
    CharField,
    Count,
    F,
    FloatField,
    Func,
    Q,
    Sum,
    TextChoices,
    Value,
)
from django.db.models.functions import Cast
from django.db.utils import OperationalError
from django.http import Http404, HttpResponse, HttpResponseBadRequest, JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.text import slugify
from django.utils.translation import gettext as _
from django.utils.translation import ngettext
from django.views import View
from django.views.decorators.http import require_GET, require_POST
from django.views.generic.edit import UpdateView
from django_tables2.export import TableExport
from vectortiles import VectorLayer
from vectortiles.views import MVTView
from waffle.decorators import waffle_flag

from commcare_connect.commcarehq.api import create_or_update_case_by_work_area
from commcare_connect.flags.flag_names import MICROPLANNING
from commcare_connect.microplanning.const import (
    MAX_EXCLUDE_WORK_AREAS,
    MAX_UNASSIGN_WORK_AREAS,
    REQUIRED_DELIVER_UNIT_SLUGS,
    WORK_AREA_STATUS_COLORS,
)
from commcare_connect.microplanning.coverage_progress import (
    IN_SCOPE_WORK_AREA,
    CoverageProgressReport,
    annotate_approved_visit_counts,
    missing_deliver_units,
)
from commcare_connect.microplanning.filters import (
    CoverageProgressFilterSet,
    UserVisitMapFilterSet,
    WorkAreaMapFilterSet,
)
from commcare_connect.microplanning.forms import AssignmentModeForm, ClusterWorkAreasForm, WorkAreaModelForm
from commcare_connect.microplanning.helpers import (
    MAP_WORK_AREA_FIELDS,
    assign_work_areas_and_sync_to_hq,
    exclude_work_areas_for_opportunity,
    map_work_areas,
    pct,
    unassign_work_areas_for_opportunity,
    work_area_detail,
    work_area_search_options,
)
from commcare_connect.microplanning.models import (
    ImplementationArea,
    InaccessibilityRequestStatus,
    WorkArea,
    WorkAreaGroup,
    WorkAreaInaccessibilityRequest,
    WorkAreaStatus,
)
from commcare_connect.microplanning.tables import CoverageWAGTable, CoverageWardTable
from commcare_connect.opportunity.models import BlobMeta, OpportunityAccess, UserVisit, VisitValidationStatus
from commcare_connect.opportunity.tasks import send_push_notification_task
from commcare_connect.organization.decorators import (
    is_org_pm_or_all_access,
    opportunity_required,
    org_admin_required,
    org_pm_required,
)
from commcare_connect.utils.celery import CELERY_TASK_FAILURE, CELERY_TASK_SUCCESS
from commcare_connect.utils.commcarehq_api import CommCareHQAPIException
from commcare_connect.utils.file import get_file_extension

from .tasks import (
    ImplementationAreaCSVImporter,
    WorkAreaCSVExporter,
    WorkAreaCSVImporter,
    cluster_work_areas_task,
    get_cluster_area_cache_lock_key,
    get_implementation_area_import_cache_key,
    get_import_area_cache_key,
    import_implementation_areas_task,
    import_work_areas_task,
    send_work_area_assignment_notification,
)

logger = logging.getLogger(__name__)

WORKAREA_MIN_ZOOM = 6

STATEMENT_TIMEOUT = "30s"
PG_QUERY_CANCELED = "57014"  # SQLSTATE raised when statement_timeout cancels a query


@require_GET
@org_admin_required
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

    show_area_btn = not (cache.get(get_import_area_cache_key(opportunity.id)) is not None or areas_present)
    show_workarea_groups_btn = areas_present and not work_area_groups_present
    show_clear_work_areas_btn = areas_present and not areas_assigned
    show_rerun_clear_work_area_groups_btn = areas_present and not areas_assigned and work_area_groups_present
    show_implementation_area_btn = not (
        cache.get(get_implementation_area_import_cache_key(opportunity.id)) is not None or implementation_areas_present
    )
    clear_data_details = get_clear_data_details(
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

    search_options_url = reverse(
        "microplanning:search_options",
        kwargs={"org_slug": request.org.slug, "opp_id": opportunity.opportunity_id},
    )

    work_area_detail_url = reverse(
        "microplanning:work_area_detail",
        args=[request.org.slug, opportunity.opportunity_id, 0],
    ).replace("/0/", "/")

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

    is_program_manager = is_org_pm_or_all_access(request)
    assignment_mode = is_program_manager and bool(request.GET.get("assignment_mode"))
    inaccessible_mode = is_program_manager and bool(request.GET.get("inaccessible_mode"))
    inaccessible_request_count = (
        WorkArea.objects.filter(opportunity_id=opportunity.id, status=WorkAreaStatus.REQUEST_FOR_INACCESSIBLE).count()
        if is_program_manager
        else 0
    )

    filterset = WorkAreaMapFilterSet(
        data=request.GET,
        opportunity=opportunity,
    )

    context = {
        "show_area_btn": show_area_btn,
        "show_implementation_area_btn": show_implementation_area_btn,
        "implementation_areas_present": implementation_areas_present,
        "show_workarea_groups_btn": show_workarea_groups_btn,
        "show_clear_work_areas_btn": show_clear_work_areas_btn,
        "clear_data_details": clear_data_details,
        "show_rerun_clear_work_area_groups_btn": show_rerun_clear_work_area_groups_btn,
        "clustering_is_rerun": show_rerun_clear_work_area_groups_btn,
        "mapbox_api_key": settings.MAPBOX_TOKEN,
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
        "metrics": get_metrics_for_microplanning(opportunity),
        "tiles_url": tiles_url,
        "visit_tiles_url": visit_tiles_url,
        "groups_url": groups_url,
        "implementation_areas_url": implementation_areas_url,
        "status_meta": status_meta,
        "workarea_min_zoom": WORKAREA_MIN_ZOOM,
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
        "is_program_manager": is_program_manager,
        "assignment_mode": assignment_mode,
        "inaccessible_mode": inaccessible_mode,
        "inaccessible_request_count": inaccessible_request_count,
        "quoted_missing_deliver_units": _quoted_missing_deliver_units(opportunity),
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


def get_clear_data_details(*, work_area_count, work_area_group_count, implementation_area_count, areas_assigned):
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


def get_metrics_for_microplanning(opportunity):
    qs = annotate_approved_visit_counts(WorkArea.objects.filter(opportunity=opportunity), opportunity, ncwa=True)

    # REQUEST_FOR_INACCESSIBLE counts as inaccessible too for metrics pupose,
    #  even though it isn't a final status.
    inaccessible_status = Q(status__in=(WorkAreaStatus.INACCESSIBLE, WorkAreaStatus.REQUEST_FOR_INACCESSIBLE))
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
            filter=IN_SCOPE_WORK_AREA & (visited_children_found | visited_no_children_found | inaccessible_status),
        ),
        unvisited=Count("id", filter=IN_SCOPE_WORK_AREA & Q(status=WorkAreaStatus.NOT_VISITED)),
        visited_children_found=Count("id", filter=IN_SCOPE_WORK_AREA & visited_children_found),
        visited_no_children_found=Count("id", filter=IN_SCOPE_WORK_AREA & visited_no_children_found),
        evc_reached=Count(
            "id",
            # Ignore areas with no target; 0 >= 0 would otherwise mark them as delivered.
            filter=IN_SCOPE_WORK_AREA & has_evc_target & Q(hsd_count__gte=F("expected_visit_count")),
        ),
        inaccessible=Count("id", filter=IN_SCOPE_WORK_AREA & inaccessible_status),
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


def _quoted_missing_deliver_units(opportunity):
    missing_units = missing_deliver_units(opportunity, list(REQUIRED_DELIVER_UNIT_SLUGS))
    return ", ".join([f'"{unit}"' for unit in missing_units])


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


@method_decorator([org_admin_required, opportunity_required, waffle_flag(MICROPLANNING)], name="dispatch")
class WorkAreaImport(View):
    def get(self, request, *args, **kwargs):
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="work_area_template.csv"'
        writer = csv.writer(response)
        writer.writerow(
            [
                *WorkAreaCSVImporter.HEADERS.values(),
                WorkAreaCSVImporter.OPTIONAL_HEADERS["implementation_area"],
                WorkAreaCSVImporter.GROUP_NAME_HEADER,
            ]
        )
        writer.writerow(
            [
                "Work-Area-1",
                "Demo Ward",
                "77.1 28.6",
                "POLYGON((77 28,78 28,78 29,77 29,77 28))",
                10,
                12,
                7,
                "LGA1",
                "State1",
                "Ward North",
                "Work-Area-Group-1",
            ]
        )
        return response

    def post(self, request, org_slug, opp_id):
        redirect_url = reverse(
            "microplanning:microplanning_home",
            kwargs={"org_slug": org_slug, "opp_id": opp_id},
        )

        csv_file = request.FILES.get("csv_file")
        if not csv_file or get_file_extension(csv_file).lower() != "csv":
            messages.error(request, _("Unsupported file format. Please upload a CSV file."))
            return redirect(redirect_url)

        if WorkArea.objects.filter(opportunity_id=request.opportunity.id).exists():
            messages.error(request, _("Work Areas already exist for this opportunity."))
            return redirect(redirect_url)

        lock_key = get_import_area_cache_key(request.opportunity.id)

        if cache.get(lock_key):
            messages.error(request, _("An import for this opportunity is already in progress."))
            return redirect(redirect_url)

        file_name = f"work_area_upload-{request.opportunity.id}-{uuid.uuid4().hex}.csv"
        default_storage.save(file_name, ContentFile(csv_file.read()))
        task = import_work_areas_task.delay(request.opportunity.id, file_name)
        cache.set(lock_key, task.id, timeout=1200)
        messages.info(request, _("Work Area upload has been started."))
        redirect_url += f"?task_id={task.id}"
        return redirect(redirect_url)


@method_decorator([org_admin_required, opportunity_required, waffle_flag(MICROPLANNING)], name="dispatch")
class ImplementationAreaImport(View):
    def get(self, request, *args, **kwargs):
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="implementation_area_template.csv"'
        writer = csv.writer(response)
        writer.writerow(ImplementationAreaCSVImporter.HEADERS.values())
        writer.writerow(["Ward North", "77.1 28.6", "POLYGON((77 28,78 28,78 29,77 29,77 28))"])
        return response

    def post(self, request, org_slug, opp_id):
        redirect_url = reverse(
            "microplanning:microplanning_home",
            kwargs={"org_slug": org_slug, "opp_id": opp_id},
        )
        if ImplementationArea.objects.filter(opportunity_id=request.opportunity.id).exists():
            messages.error(request, _("Implementation Areas already exist for this opportunity."))
            return redirect(redirect_url)

        lock_key = get_implementation_area_import_cache_key(request.opportunity.id)
        if cache.get(lock_key):
            messages.error(request, _("An import for this opportunity is already in progress."))
            return redirect(redirect_url)

        csv_file = request.FILES.get("csv_file")
        if not csv_file or get_file_extension(csv_file).lower() != "csv":
            messages.error(request, _("Unsupported file format. Please upload a CSV file."))
            return redirect(redirect_url)

        file_name = f"implementation_area_upload-{request.opportunity.id}-{uuid.uuid4().hex}.csv"
        default_storage.save(file_name, ContentFile(csv_file.read()))
        task = import_implementation_areas_task.delay(request.opportunity.id, file_name)
        cache.set(lock_key, task.id, timeout=1200)
        messages.info(request, _("Implementation Area upload has been started."))
        redirect_url += f"?task_id={task.id}&area_type=implementation_area"
        return redirect(redirect_url)


@require_POST
@org_admin_required
@opportunity_required
@waffle_flag(MICROPLANNING)
def clear_implementation_areas(request, org_slug, opp_id):
    # Delete the opportunity's Implementation Areas so a fresh set can be uploaded. Work Areas
    # keep their implementation_area_name (the FK is set null via SET_NULL) and re-link on the
    # next Implementation Area upload.
    ImplementationArea.objects.filter(opportunity_id=request.opportunity.id).delete()
    messages.success(request, _("Implementation Areas cleared. You can now upload a new file."))
    redirect_url = reverse("microplanning:microplanning_home", kwargs={"org_slug": org_slug, "opp_id": opp_id})
    return HttpResponse(headers={"HX-Redirect": redirect_url})


def _area_modal_context(org_slug, opp_id, area_type):
    if area_type == "implementation_area":
        return {
            "modal_title": _("Upload Implementation Area"),
            "records_label": _("Implementation Areas"),
            "success_noun": _("implementation area(s)"),
            "upload_url": reverse(
                "microplanning:upload_implementation_areas", kwargs={"org_slug": org_slug, "opp_id": opp_id}
            ),
            "status_url": reverse(
                "microplanning:implementation_area_import_status", kwargs={"org_slug": org_slug, "opp_id": opp_id}
            ),
            "column_requirements": [
                _("Implementation Area Name – unique name of the ward/district"),
                _("Centroid – longitude and latitude separated by space (e.g. 77.123 28.456)"),
                _("Boundary – Polygon in WKT format"),
            ],
        }
    return {
        "modal_title": _("Upload Work Areas"),
        "records_label": _("Work Areas"),
        "success_noun": _("work area(s)"),
        "upload_url": reverse("microplanning:upload_work_areas", kwargs={"org_slug": org_slug, "opp_id": opp_id}),
        "status_url": reverse("microplanning:import_status", kwargs={"org_slug": org_slug, "opp_id": opp_id}),
        "column_requirements": [
            _("Area Slug – unique identifier for each work area should be unique in an opportunity"),
            _("Ward – name of the ward"),
            _("Centroid – longitude and latitude separated by space (e.g. 77.123 28.456)"),
            _("Boundary – Polygon in WKT format"),
            _("Building Count – positive integer"),
            _("Expected Visit Count – positive integer"),
            _("LGA – name of the LGA the work area is in"),
            _("State – name of the state the work area is in"),
            _("Implementation Area – (optional) name of the matching Implementation Area"),
            _(
                "Work Area Group Name (optional): Enter a group name for every row to assign work areas directly "
                "and skip automatic clustering. Leave this column blank for every row to use automatic clustering. "
                "Do not mix blank and filled values."
            ),
        ],
    }


@org_admin_required
@opportunity_required
@waffle_flag(MICROPLANNING)
def import_status(request, org_slug, opp_id, area_type="work_area"):
    task_id = request.GET.get("task_id", None)
    result_ready = False
    result_data = None

    if task_id:
        try:
            task_id = uuid.UUID(task_id)
        except (ValueError, TypeError):
            return redirect(
                reverse("microplanning:microplanning_home", kwargs={"org_slug": org_slug, "opp_id": opp_id})
            )
        result = AsyncResult(str(task_id))
        result_ready = result.ready()
        if result_ready:
            if result.successful():
                result_data = result.result
            else:
                result_data = {"errors": {_("Import failed due to an internal error. Please try again."): [0]}}

    context = {
        "result_ready": result_ready,
        "result_data": result_data,
        "task_id": task_id,
        **_area_modal_context(org_slug, opp_id, area_type),
    }
    return render(request, "microplanning/import_area_modal.html", context)


class WorkAreaVectorLayer(VectorLayer):
    id = "workareas"
    # Same fields the search box's sidebar detail serves, so a work area reads identically
    # however the sidebar was populated.
    tile_fields = MAP_WORK_AREA_FIELDS
    geom_field = "boundary"
    min_zoom = WORKAREA_MIN_ZOOM

    def __init__(self, *args, opportunity=None, filter_params=None, **kwargs):
        self.opportunity = opportunity
        self.filter_params = filter_params
        super().__init__(*args, **kwargs)

    def get_queryset(self):
        qs = map_work_areas(self.opportunity)
        return WorkAreaMapFilterSet(self.filter_params, queryset=qs, opportunity=self.opportunity).qs


@method_decorator([org_admin_required, opportunity_required, waffle_flag(MICROPLANNING)], name="dispatch")
class WorkAreaTileView(MVTView):
    layer_classes = [WorkAreaVectorLayer]

    def get_layers(self):
        return [
            WorkAreaVectorLayer(
                opportunity=self.request.opportunity,
                filter_params=self.request.GET,
            )
        ]


class UserVisitVectorLayer(VectorLayer):
    id = "user-visits"
    tile_fields = ("work_area_id", "visit_uuid")
    geom_field = "location_point"
    min_zoom = WORKAREA_MIN_ZOOM

    def __init__(self, *args, opportunity=None, filter_params=None, **kwargs):
        self.opportunity = opportunity
        self.filter_params = filter_params
        super().__init__(*args, **kwargs)

    def get_queryset(self):
        """
        Returns the user visits with location_point annotated.

        The user visit location is assumed to be a string in the format:
        <lat> <lng> <altitude> <accuracy>
        """
        qs = UserVisit.objects.filter(
            opportunity=self.opportunity,
            location__isnull=False,
        ).exclude(location="")
        qs = UserVisitMapFilterSet(self.filter_params, queryset=qs, opportunity=self.opportunity).qs
        return (
            qs.annotate(
                lat=Cast(Func(F("location"), Value(" "), Value(1), function="split_part"), output_field=FloatField()),
                lon=Cast(Func(F("location"), Value(" "), Value(2), function="split_part"), output_field=FloatField()),
            )
            .annotate(
                location_point=Func(
                    Func(F("lon"), F("lat"), function="ST_MakePoint"),
                    Value(4326),
                    function="ST_SetSRID",
                    output_field=PointField(srid=4326),
                ),
                visit_uuid=Cast("user_visit_id", output_field=CharField()),
            )
            .values("location_point", "work_area_id", "visit_uuid")
        )


@method_decorator([org_admin_required, opportunity_required, waffle_flag(MICROPLANNING)], name="dispatch")
class UserVisitTileView(MVTView):
    layer_classes = [UserVisitVectorLayer]

    def get_layers(self):
        return [
            UserVisitVectorLayer(
                opportunity=self.request.opportunity,
                filter_params=self.request.GET,
            )
        ]


@org_admin_required
@opportunity_required
@waffle_flag(MICROPLANNING)
def workareas_group_geojson(request, org_slug, opp_id):
    # This view aggregates group boundaries for map display.
    # To be removed in https://dimagi.atlassian.net/browse/CCCT-2213 for a better performant alternative

    qs = WorkArea.objects.filter(opportunity_id=request.opportunity.id)

    group_features = [
        {
            "type": "Feature",
            "geometry": json.loads(g["geojson"]),
            "properties": {"group_id": g["group_id"]},
        }
        for g in (
            qs.filter(work_area_group__isnull=False)
            .values(group_id=F("work_area_group__id"))
            .annotate(geojson=AsGeoJSON(Union("boundary")))
        )
    ]
    extent = qs.aggregate(extent=Extent("boundary"))["extent"]
    return JsonResponse({"group_features": group_features, "workarea_bounds": extent})


@require_GET
@org_admin_required
@opportunity_required
@waffle_flag(MICROPLANNING)
def search_options(request, org_slug, opp_id):
    """Options for the map's work area search box, fetched once after the page loads."""
    return JsonResponse({"options": work_area_search_options(request.opportunity)})


@require_GET
@org_admin_required
@opportunity_required
@waffle_flag(MICROPLANNING)
def work_area_detail_json(request, org_slug, opp_id, work_area_id):
    """One work area's details, so picking it in the search box fills the sidebar without a
    map click — the searched area is often outside the current viewport."""
    detail = work_area_detail(request.opportunity, work_area_id)
    if detail is None:
        raise Http404("Work area not found")
    return JsonResponse(detail)


@org_admin_required
@opportunity_required
@waffle_flag(MICROPLANNING)
def implementation_areas_geojson(request, org_slug, opp_id):
    # Implementation Areas render as a standalone visual layer on the progress map. They are
    # independent of Work Areas (Work Area boxes may fall outside these boundaries), so this is
    # kept separate from workareas_group_geojson and does not affect the map's auto-zoom.
    features = [
        {
            "type": "Feature",
            "geometry": json.loads(ia["geojson"]),
            "properties": {"name": ia["name"]},
        }
        for ia in (
            ImplementationArea.objects.filter(opportunity_id=request.opportunity.id)
            .values("name")
            .annotate(geojson=AsGeoJSON("boundary"))
        )
    ]
    return JsonResponse({"implementation_area_features": features})


@org_admin_required
@opportunity_required
@require_POST
@waffle_flag(MICROPLANNING)
def cluster_work_areas(request, org_slug, opp_id):
    redirect_url = reverse(
        "microplanning:microplanning_home",
        kwargs={"org_slug": org_slug, "opp_id": opp_id},
    )

    rerun = request.GET.get("rerun") is not None
    if rerun:
        if WorkArea.objects.filter(opportunity_id=request.opportunity.id, opportunity_access__isnull=False).exists():
            messages.error(
                request, _("Clustering cannot be re-run because Work Areas have already been assigned to FLWs.")
            )
            return HttpResponse(headers={"HX-Redirect": redirect_url})
        WorkAreaGroup.objects.filter(opportunity_id=request.opportunity.id).delete()

    if not WorkArea.objects.filter(opportunity_id=request.opportunity.id).exists():
        messages.error(request, _("Please upload Work Areas for this opportunity."))
        return HttpResponse(headers={"HX-Redirect": redirect_url})

    if WorkAreaGroup.objects.filter(opportunity_id=request.opportunity.id).exists():
        messages.error(request, _("Work Area Groups already exist for this opportunity."))
        return HttpResponse(headers={"HX-Redirect": redirect_url})

    form = ClusterWorkAreasForm(request.POST)
    if not form.is_valid():
        # Retarget the swap onto the field container so the error renders in
        # place, keeping the Create Groups button and the modal untouched.
        response = HttpResponse(render_crispy_form(form))
        response.headers["HX-Retarget"] = "#building-count-field"
        response.headers["HX-Reswap"] = "outerHTML"
        return response

    lock_key = get_cluster_area_cache_lock_key(request.opportunity.id)
    if cache.lock(lock_key).locked():
        messages.error(request, _("Work Area Clustering is already in progress for this opportunity."))
        return HttpResponse(headers={"HX-Redirect": redirect_url})

    task = cluster_work_areas_task.delay(request.opportunity.id, form.cleaned_data["building_count"])
    redirect_url += f"?clustering_task_id={task.id}"
    response = render(
        request,
        "microplanning/cluster_work_area_modal_status.html",
        context={"clustering_task_id": task.id},
    )
    response.headers["HX-Push-Url"] = redirect_url
    return response


@org_admin_required
@opportunity_required
def clustering_status(request, org_slug, opp_id):
    task_id = request.GET.get("clustering_task_id", None)
    redirect_url = reverse("microplanning:microplanning_home", args=(org_slug, opp_id))

    if task_id:
        try:
            uuid.UUID(task_id)
        except (ValueError, TypeError):
            return redirect("microplanning:microplanning_home", org_slug=org_slug, opp_id=opp_id)

        task = AsyncResult(task_id)
        status = task.state
        message = None
        icon = None
        refresh_page = False

        if status == CELERY_TASK_SUCCESS:
            message = _("Work Area Clustering was successful. You may close this window.")
            icon = "fa-solid fa-circle-check text-green-600"
            refresh_page = True
            messages.success(request, "Work Area Clustering was successful.")
        elif status == CELERY_TASK_FAILURE:
            message = _("There was an error. Please try again.")
            icon = "fa-solid fa-circle-exclamation text-red-600"
        else:
            # htmx does not swap content when status 204 is returned.
            # This keeps the progress bar intact, once any of the above
            # status are triggered, the progress bar is replaced with a
            # non-refreshing div to show final status.
            return HttpResponse(status=HTTPStatus.NO_CONTENT)

        response = render(
            request,
            "microplanning/cluster_work_area_final_status.html",
            context={"icon": icon, "message": message},
        )
        if refresh_page:
            response.headers["HX-Redirect"] = redirect_url
        return response

    return HttpResponse(headers={"HX-Redirect": redirect_url})


@require_POST
@org_admin_required
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

    result = exclude_work_areas_for_opportunity(
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


@require_POST
@org_admin_required
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


@require_POST
@org_admin_required
@opportunity_required
@waffle_flag(MICROPLANNING)
def clear_work_area_groups(request, org_slug, opp_id):
    redirect_url = reverse(
        "microplanning:microplanning_home",
        kwargs={"org_slug": org_slug, "opp_id": opp_id},
    )
    if WorkArea.objects.filter(opportunity_id=request.opportunity.id, opportunity_access__isnull=False).exists():
        messages.error(request, _("Work Areas already assigned to users. Work Area Groups cannot be cleared."))
        return HttpResponse(headers={"HX-Redirect": redirect_url})

    WorkAreaGroup.objects.filter(opportunity_id=request.opportunity.id).delete()
    messages.success(request, _("Work Area Groups have been cleared for this opportunity."))
    return HttpResponse(headers={"HX-Redirect": redirect_url})


@require_GET
@org_admin_required
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


@method_decorator([org_admin_required, opportunity_required, waffle_flag(MICROPLANNING)], name="dispatch")
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


@require_GET
@org_pm_required
@opportunity_required
@waffle_flag(MICROPLANNING)
def get_work_areas_for_assignment(request, org_slug, opp_id):
    group_ids = request.GET.getlist("group_id")
    work_areas = list(
        WorkArea.objects.filter(
            opportunity=request.opportunity,
            work_area_group_id__in=group_ids,
        ).values("id", "building_count", "expected_visit_count", "status", group_id=F("work_area_group_id"))
    )
    return JsonResponse({"work_areas": work_areas})


@require_GET
@org_pm_required
@opportunity_required
@waffle_flag(MICROPLANNING)
def get_flw_work_areas_for_assignment(request, org_slug, opp_id, assignee_id):
    work_areas = list(
        WorkArea.objects.filter(
            opportunity=request.opportunity,
            opportunity_access_id=assignee_id,
        ).values("id", "building_count", "expected_visit_count", "status")
    )
    return JsonResponse({"work_areas": work_areas})


@require_GET
@org_pm_required
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
@org_pm_required
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
@org_pm_required
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


@require_GET
@org_admin_required
@opportunity_required
@waffle_flag(MICROPLANNING)
def review_inaccessibility_request(request, org_slug, opp_id, work_area_id):
    work_area = get_object_or_404(
        WorkArea,
        id=work_area_id,
        opportunity=request.opportunity,
        status=WorkAreaStatus.REQUEST_FOR_INACCESSIBLE,
    )
    inacc_request = get_object_or_404(
        WorkAreaInaccessibilityRequest, work_area=work_area, status=InaccessibilityRequestStatus.PENDING
    )
    try:
        photo = BlobMeta.objects.get(parent_id=inacc_request.xform_id)
    except BlobMeta.DoesNotExist:
        photo = None
    return render(
        request,
        "microplanning/review_inaccessibility_panel.html",
        context={
            "work_area": work_area,
            "inaccessibility_request": inacc_request,
            "photo": photo,
            "boundary_geojson": json.loads(work_area.boundary.geojson),
            "request_location_geojson": (
                json.loads(inacc_request.location.geojson) if inacc_request.location else None
            ),
            "mapbox_api_key": settings.MAPBOX_TOKEN,
        },
    )


class InaccessibilityReviewAction(TextChoices):
    APPROVE = "approve", "Approve"
    DENY = "deny", "Deny"


_ACTION_TO_NEW_STATUS = {
    InaccessibilityReviewAction.APPROVE: WorkAreaStatus.INACCESSIBLE,
    InaccessibilityReviewAction.DENY: WorkAreaStatus.NOT_VISITED,
}

_ACTION_TO_REQUEST_STATUS = {
    InaccessibilityReviewAction.APPROVE: InaccessibilityRequestStatus.APPROVED,
    InaccessibilityReviewAction.DENY: InaccessibilityRequestStatus.DENIED,
}


@require_POST
@org_admin_required
@opportunity_required
@waffle_flag(MICROPLANNING)
def act_on_inaccessibility_request(request, org_slug, opp_id, work_area_id):
    try:
        action = InaccessibilityReviewAction(request.POST.get("action", ""))
    except ValueError:
        return HttpResponseBadRequest("Invalid action")

    new_status = _ACTION_TO_NEW_STATUS[action]

    work_area = get_object_or_404(
        WorkArea.objects.select_for_update(),
        id=work_area_id,
        opportunity=request.opportunity,
        status=WorkAreaStatus.REQUEST_FOR_INACCESSIBLE,
    )
    inacc_request = get_object_or_404(
        WorkAreaInaccessibilityRequest.objects.select_related("opportunity_access__user"),
        work_area=work_area,
        status=InaccessibilityRequestStatus.PENDING,
    )

    work_area.status = new_status
    inacc_request.status = _ACTION_TO_REQUEST_STATUS[action]
    try:
        with transaction.atomic():
            with pghistory.context(username=request.user.username, user_email=request.user.email):
                work_area.save(update_fields=["status"])
            inacc_request.save(update_fields=["status"])
            if work_area.work_area_group and action == InaccessibilityReviewAction.APPROVE:
                work_area.work_area_group.update_centroid()
            if work_area.opportunity_access_id:
                create_or_update_case_by_work_area(work_area)

    except CommCareHQAPIException as e:
        logger.info(f"Failed to sync work area {work_area.id} to HQ after review action. Error: {e}")
        return HttpResponse(status=500, content=_("Failed to sync work area status. Please try again."))

    if action == InaccessibilityReviewAction.DENY:
        transaction.on_commit(
            partial(
                send_push_notification_task.delay,
                [inacc_request.opportunity_access.user_id],
                "Inaccessibility Request Denied",
                "Your request to mark a work area inaccessible has been declined.",
            )
        )

    response = HttpResponse(status=204)
    response["HX-Trigger"] = json.dumps({"inaccessibilityReviewed": {"id": work_area.id, "status": new_status}})
    return response


@org_admin_required
@opportunity_required
@waffle_flag(MICROPLANNING)
def coverage_progress(request, *args, **kwargs):
    """Coverage Progress Tracker page: a header saturation goal plus the per-ward "Core Metrics"
    table and the per-work-area-group "Metrics by Work Area Group" table. Each table has its own
    download button, handled via the ``export``/``table`` query params (see ``_export_coverage_table``).
    """
    opportunity = request.opportunity
    filterset = CoverageProgressFilterSet(request.GET, queryset=WorkArea.objects.none())
    date_filter = filterset.to_date_filter()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL statement_timeout = %s", [STATEMENT_TIMEOUT])
        report = CoverageProgressReport(opportunity, date_filter)
        header = report.header()
        ward_table = CoverageWardTable(report.ward_rows())
        wag_table = CoverageWAGTable(report.wag_rows())
    except OperationalError as exc:
        # Only a statement_timeout (QueryCanceled) is an expected degradation; re-raise anything else
        # (e.g. a real connection error) so it isn't masked as a timeout.
        if getattr(exc.__cause__, "pgcode", None) != PG_QUERY_CANCELED:
            raise
        # The timeout aborted the txn; roll back (else ATOMIC_REQUESTS' COMMIT fails) and return a
        # query-free 503 — rendering base.html here would re-hit the DB via its context processors.
        transaction.set_rollback(True)
        logger.exception("Coverage progress query timed out for opportunity %s", opportunity.id)
        return HttpResponse(
            _("Report timed out. Please reach out to support if the error persists."),
            status=503,
            content_type="text/plain",
        )

    tables = {"ward": ward_table, "wag": wag_table}
    export_response = _export_coverage_table(request, opportunity, tables)
    if export_response is not None:
        return export_response

    # Pre-build the per-table download links so each carries the active filter (a download then
    # matches the on-screen filtered view rather than silently exporting the overall report). The
    # export param names come from the same constants the export view reads, so they can't drift.
    export_hrefs = {
        table_key: {
            fmt: "?"
            + filterset.export_querystring({COVERAGE_EXPORT_FORMAT_PARAM: fmt, COVERAGE_EXPORT_TABLE_PARAM: table_key})
            for fmt in ("csv", "xlsx")
        }
        for table_key in tables
    }

    context = {
        "opportunity": opportunity,
        "header": header,
        "ward_table": ward_table,
        "wag_table": wag_table,
        "filter_form": filterset.form,
        "export_hrefs": export_hrefs,
        "quoted_missing_deliver_units": _quoted_missing_deliver_units(opportunity),
        "path": [
            {"title": _("Opportunities"), "url": reverse("opportunity:list", kwargs={"org_slug": request.org.slug})},
            {
                "title": opportunity.name,
                "url": reverse("opportunity:detail", args=(request.org.slug, opportunity.opportunity_id)),
            },
            {
                "title": _("Progress Map"),
                "url": reverse(
                    "microplanning:microplanning_home", args=(request.org.slug, opportunity.opportunity_id)
                ),
            },
            {"title": _("Progress Tracker")},
        ],
    }
    return render(request, "microplanning/coverage_progress.html", context)


# Query params used by the per-table download buttons: ``?export=<format>&table=<ward|wag>``.
COVERAGE_EXPORT_FORMAT_PARAM = "export"
COVERAGE_EXPORT_TABLE_PARAM = "table"
DEFAULT_COVERAGE_EXPORT_TABLE = "ward"
# Maps each ``table`` value to the file-name stem used in the download.
COVERAGE_EXPORT_FILENAME_STEMS = {"ward": "core_metrics", "wag": "metrics_by_work_area_group"}


def _export_coverage_table(request, opportunity, tables):
    """Return a file response for the requested table/format, or None if no export was requested.

    ``tables`` maps a ``table`` value (e.g. "ward"/"wag") to its built table. An unsupported
    ``export`` format or an unknown ``table`` value returns a 400 rather than silently serving
    the wrong table.
    """
    export_format = request.GET.get(COVERAGE_EXPORT_FORMAT_PARAM)
    if not export_format:
        return None
    if not TableExport.is_valid_format(export_format):
        return HttpResponseBadRequest(_("Unsupported export format."))

    export_table = request.GET.get(COVERAGE_EXPORT_TABLE_PARAM, DEFAULT_COVERAGE_EXPORT_TABLE)
    if export_table not in tables:
        return HttpResponseBadRequest(_("Unknown table."))

    exporter = TableExport(export_format, tables[export_table])
    return exporter.response(
        f"{slugify(opportunity.name)}_{COVERAGE_EXPORT_FILENAME_STEMS[export_table]}.{export_format}"
    )
