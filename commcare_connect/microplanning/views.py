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
from django.db.models import Count, F, FloatField, Func, IntegerField, OuterRef, Q, Subquery, Sum, TextChoices, Value
from django.db.models.functions import Cast, Coalesce
from django.db.utils import OperationalError
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.text import slugify
from django.utils.timezone import localdate
from django.utils.translation import gettext as _
from django.views import View
from django.views.decorators.http import require_GET, require_POST
from django.views.generic.edit import UpdateView
from django_tables2.export import TableExport
from vectortiles import VectorLayer
from vectortiles.views import MVTView
from waffle.decorators import waffle_flag

from commcare_connect.commcarehq.api import (
    bulk_create_or_update_cases_by_work_areas,
    create_or_update_case_by_work_area,
)
from commcare_connect.flags.flag_names import MICROPLANNING
from commcare_connect.microplanning.const import (
    MAX_EXCLUDE_WORK_AREAS,
    MAX_UNASSIGN_WORK_AREAS,
    WORK_AREA_STATUS_COLORS,
)
from commcare_connect.microplanning.coverage_progress import CoverageProgressReport
from commcare_connect.microplanning.filters import (
    CoverageProgressFilterSet,
    UserVisitMapFilterSet,
    WorkAreaMapFilterSet,
)
from commcare_connect.microplanning.forms import AssignmentModeForm, ClusterWorkAreasForm, WorkAreaModelForm
from commcare_connect.microplanning.helpers import (
    exclude_work_areas_for_opportunity,
    pct,
    unassign_work_areas_for_opportunity,
)
from commcare_connect.microplanning.models import (
    WorkArea,
    WorkAreaGroup,
    WorkAreaInaccessibilityRequest,
    WorkAreaStatus,
)
from commcare_connect.microplanning.tables import CoverageWAGTable, CoverageWardTable
from commcare_connect.opportunity.models import BlobMeta, OpportunityAccess, UserVisit, VisitValidationStatus
from commcare_connect.opportunity.tasks import send_push_notification_task
from commcare_connect.organization.decorators import (
    opportunity_required,
    org_admin_required,
    org_program_manager_required,
    request_user_is_program_manager,
)
from commcare_connect.utils.celery import CELERY_TASK_FAILURE, CELERY_TASK_SUCCESS
from commcare_connect.utils.commcarehq_api import CommCareHQAPIException
from commcare_connect.utils.file import get_file_extension

from .tasks import (
    WorkAreaCSVExporter,
    WorkAreaCSVImporter,
    cluster_work_areas_task,
    get_cluster_area_cache_lock_key,
    get_import_area_cache_key,
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
    areas_present = WorkArea.objects.filter(opportunity_id=opportunity.id).exists()
    areas_assigned = WorkArea.objects.filter(opportunity_id=opportunity.id, opportunity_access__isnull=False).exists()
    work_area_groups_present = WorkAreaGroup.objects.filter(opportunity_id=opportunity.id).exists()

    show_area_btn = not (cache.get(get_import_area_cache_key(opportunity.id)) is not None or areas_present)
    show_workarea_groups_btn = areas_present and not work_area_groups_present
    show_rerun_clear_work_area_groups_btn = areas_present and not areas_assigned and work_area_groups_present

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

    edit_work_area_url = reverse(
        "microplanning:modify_work_area",
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

    status_meta = {
        status.value: {
            "label": status.label,
            "class": WORK_AREA_STATUS_COLORS.get(status),
        }
        for status in WorkAreaStatus
    }

    is_program_manager = request_user_is_program_manager(request)
    assignment_mode = is_program_manager and bool(request.GET.get("assignment_mode"))

    filterset = WorkAreaMapFilterSet(
        data=request.GET,
        opportunity=opportunity,
    )

    context = {
        "show_area_btn": show_area_btn,
        "show_workarea_groups_btn": show_workarea_groups_btn,
        "show_rerun_clear_work_area_groups_btn": show_rerun_clear_work_area_groups_btn,
        "clustering_is_rerun": show_rerun_clear_work_area_groups_btn,
        "mapbox_api_key": settings.MAPBOX_TOKEN,
        "task_id": request.GET.get("task_id"),
        "opportunity": opportunity,
        "metrics": get_metrics_for_microplanning(opportunity),
        "tiles_url": tiles_url,
        "visit_tiles_url": visit_tiles_url,
        "groups_url": groups_url,
        "status_meta": status_meta,
        "workarea_min_zoom": WORKAREA_MIN_ZOOM,
        "edit_work_area_url": edit_work_area_url,
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
    }

    if assignment_mode:
        context.update(_get_assignment_mode_context(request, opportunity))

    return render(
        request,
        template_name="microplanning/home.html",
        context=context,
    )


def get_metrics_for_microplanning(opportunity):
    approved_visits_for_work_area = (
        UserVisit.objects.filter(
            opportunity=opportunity,
            work_area=OuterRef("pk"),
            status=VisitValidationStatus.approved,
        )
        .values("work_area")
        .annotate(c=Count("*"))
        .values("c")
    )

    qs = WorkArea.objects.filter(opportunity=opportunity).annotate(
        approved_count=Coalesce(
            Subquery(approved_visits_for_work_area, output_field=IntegerField()),
            0,
        )
    )

    non_excluded = ~Q(status=WorkAreaStatus.EXCLUDED)
    agg = qs.aggregate(
        total=Count("id"),
        excluded=Count("id", filter=Q(status=WorkAreaStatus.EXCLUDED)),
        non_excluded=Count("id", filter=non_excluded),
        unvisited=Count("id", filter=non_excluded & Q(approved_count=0)),
        visited=Count("id", filter=non_excluded & Q(approved_count__gte=1)),
        evc_reached=Count(
            "id",
            filter=non_excluded & Q(approved_count__gte=F("expected_visit_count")),
        ),
        inaccessible=Count("id", filter=Q(status=WorkAreaStatus.INACCESSIBLE)),
        total_expected_visits=Sum("expected_visit_count", filter=non_excluded),
        total_approved_visits=Sum("approved_count", filter=non_excluded),
    )

    non_excluded_count = agg["non_excluded"] or 0
    total = agg["total"] or 0

    total_expected = agg["total_expected_visits"] or 0
    if non_excluded_count and total_expected:
        total_approved_visits = agg["total_approved_visits"] or 0
        pct_wa_visited = (agg["visited"] or 0) / non_excluded_count
        pct_visits = total_approved_visits / total_expected
        visited_to_visits = round(pct_wa_visited / pct_visits, 2) if pct_visits else "--"
    else:
        visited_to_visits = "--"

    days_remaining = max((opportunity.end_date - localdate()).days, 0) if opportunity.end_date else "--"

    return [
        {"name": _("Days Remaining"), "value": days_remaining},
        {
            "name": _("Unvisited Work Areas"),
            "value": agg["unvisited"],
            "percentage": pct(agg["unvisited"], non_excluded_count, ndigits=None),
        },
        {
            "name": _("Visited Work Areas"),
            "value": agg["visited"],
            "percentage": pct(agg["visited"], non_excluded_count, ndigits=None),
        },
        {
            "name": _("EVC Reached"),
            "value": agg["evc_reached"],
            "percentage": pct(agg["evc_reached"], non_excluded_count, ndigits=None),
        },
        {
            "name": _("Inaccessible Work Areas"),
            "value": agg["inaccessible"],
            "percentage": pct(agg["inaccessible"], non_excluded_count, ndigits=None),
        },
        {
            "name": _("Excluded Work Areas"),
            "value": agg["excluded"],
            "percentage": pct(agg["excluded"], total, ndigits=None),
        },
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
            .values("id", "user__name", "user__user_id")
        ),
        "group_work_areas_url": reverse(
            "microplanning:get_work_areas_for_assignment",
            args=[org_slug, opp_id, 0],
        ).replace("/0/", "/__group_id__/"),
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
            "opportunity:worker_list",
            args=[org_slug, opp_id],
        ),
    }


@method_decorator([org_admin_required, opportunity_required, waffle_flag(MICROPLANNING)], name="dispatch")
class WorkAreaImport(View):
    def get(self, request, *args, **kwargs):
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="work_area_template.csv"'
        writer = csv.writer(response)
        writer.writerow(WorkAreaCSVImporter.HEADERS.values())
        writer.writerow(
            [
                "Work-Area-1",
                "Demo Ward",
                "77.1 28.6",
                "POLYGON((77 28,78 28,78 29,77 29,77 28))",
                10,
                12,
                7,
                2,
                "LGA1",
                "State1",
            ]
        )
        return response

    def post(self, request, org_slug, opp_id):
        redirect_url = reverse(
            "microplanning:microplanning_home",
            kwargs={"org_slug": org_slug, "opp_id": opp_id},
        )

        if WorkArea.objects.filter(opportunity_id=request.opportunity.id).exists():
            messages.error(request, _("Work Areas already exist for this opportunity."))
            return redirect(redirect_url)

        lock_key = get_import_area_cache_key(request.opportunity.id)

        if cache.get(lock_key):
            messages.error(request, _("An import for this opportunity is already in progress."))
            return redirect(redirect_url)

        csv_file = request.FILES.get("csv_file")
        if not csv_file or get_file_extension(csv_file).lower() != "csv":
            messages.error(request, _("Unsupported file format. Please upload a CSV file."))
            return redirect(redirect_url)

        file_name = f"work_area_upload-{request.opportunity.id}-{uuid.uuid4().hex}.csv"
        default_storage.save(file_name, ContentFile(csv_file.read()))
        task = import_work_areas_task.delay(request.opportunity.id, file_name)
        cache.set(lock_key, task.id, timeout=1200)
        messages.info(request, _("Work Area upload has been started."))
        redirect_url += f"?task_id={task.id}"
        return redirect(redirect_url)


@org_admin_required
@opportunity_required
@waffle_flag(MICROPLANNING)
def import_status(request, org_slug, opp_id):
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

    context = {"result_ready": result_ready, "result_data": result_data, "task_id": task_id}

    return render(request, "microplanning/import_work_area_modal.html", context)


class WorkAreaVectorLayer(VectorLayer):
    id = "workareas"
    tile_fields = ("id", "status", "building_count", "expected_visit_count", "group_id", "group_name", "assignee_name")
    geom_field = "boundary"
    min_zoom = WORKAREA_MIN_ZOOM

    def __init__(self, *args, opportunity=None, filter_params=None, **kwargs):
        self.opportunity = opportunity
        self.filter_params = filter_params
        super().__init__(*args, **kwargs)

    def get_queryset(self):
        qs = WorkArea.objects.filter(opportunity=self.opportunity).annotate(
            group_id=F("work_area_group__id"),
            group_name=F("work_area_group__name"),
            assignee_name=F("opportunity_access__user__name"),
        )
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
    tile_fields = ("work_area_id",)
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
                )
            )
            .values("location_point", "work_area_id")
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


@org_admin_required
@opportunity_required
@require_POST
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
        try:
            with transaction.atomic(), pghistory.context(reason=reason):
                work_area.save(update_fields=["expected_visit_count", "work_area_group"])
                if "expected_visit_count" in form.changed_data:
                    work_area.update_status()
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

        response = HttpResponse(status=204)
        response["HX-Trigger"] = json.dumps(
            {
                "workAreaUpdated": {
                    "id": work_area.id,
                    "expected_visit_count": work_area.expected_visit_count,
                    "group_id": work_area.work_area_group_id,
                    "group_name": getattr(work_area.work_area_group, "name", None),
                }
            }
        )
        return response


@require_GET
@org_program_manager_required
@opportunity_required
@waffle_flag(MICROPLANNING)
def get_work_areas_for_assignment(request, org_slug, opp_id, group_id):
    work_areas = list(
        WorkArea.objects.filter(
            opportunity=request.opportunity,
            work_area_group_id=group_id,
        ).values("id", "building_count", "expected_visit_count", "status")
    )
    return JsonResponse({"work_areas": work_areas})


@require_GET
@org_program_manager_required
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
@org_program_manager_required
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
@org_program_manager_required
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

    WorkArea.objects.bulk_update(all_work_areas, ["opportunity_access", "status"])

    try:
        bulk_create_or_update_cases_by_work_areas(all_work_areas, request.opportunity)
    except CommCareHQAPIException:
        transaction.set_rollback(True)
        return JsonResponse({"error": _("Failed to sync with CommCare HQ. Please try again.")}, status=502)

    notified_access_ids = {access.id for access in work_area_to_access.values()}
    for access_id in notified_access_ids:
        transaction.on_commit(lambda aid=access_id: send_work_area_assignment_notification.delay(aid))

    return JsonResponse({"status": "ok"})


@require_POST
@org_program_manager_required
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
    inacc_request = get_object_or_404(WorkAreaInaccessibilityRequest, work_area=work_area)
    try:
        photo = BlobMeta.objects.get(parent_id=inacc_request.xform_id)
    except BlobMeta.DoesNotExist:
        photo = None
    return render(
        request,
        "microplanning/review_inaccessibility_modal.html",
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
    )

    work_area.status = new_status
    try:
        with transaction.atomic():
            with pghistory.context(username=request.user.username, user_email=request.user.email):
                work_area.save(update_fields=["status"])
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
        "path": [
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
