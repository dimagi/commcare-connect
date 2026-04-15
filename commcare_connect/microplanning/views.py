import csv
import json
import logging
import uuid
from http import HTTPStatus

import pghistory
from celery.result import AsyncResult
from django.conf import settings
from django.contrib import messages
from django.contrib.gis.db.models import Extent, Union
from django.contrib.gis.db.models.fields import PointField
from django.contrib.gis.db.models.functions import AsGeoJSON
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import F, FloatField, Func, Value
from django.db.models.functions import Cast
from django.http import HttpResponse, JsonResponse, StreamingHttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.timezone import localdate
from django.utils.translation import gettext as _
from django.views import View
from django.views.decorators.http import require_GET, require_POST
from django.views.generic.edit import UpdateView
from vectortiles import VectorLayer
from vectortiles.views import MVTView

from commcare_connect.commcarehq.api import create_or_update_case_by_work_area
from commcare_connect.flags.decorators import require_flag_for_opp
from commcare_connect.flags.flag_names import MICROPLANNING
from commcare_connect.microplanning.const import WORK_AREA_STATUS_COLORS
from commcare_connect.microplanning.filters import UserVisitMapFilterSet, WorkAreaMapFilterSet
from commcare_connect.microplanning.forms import WorkAreaModelForm
from commcare_connect.microplanning.models import WorkArea, WorkAreaGroup, WorkAreaStatus
from commcare_connect.opportunity.models import UserVisit
from commcare_connect.organization.decorators import opportunity_required, org_admin_required
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
)

logger = logging.getLogger(__name__)

WORKAREA_MIN_ZOOM = 6


@require_GET
@org_admin_required
@opportunity_required
@require_flag_for_opp(MICROPLANNING)
def microplanning_home(request, *args, **kwargs):
    opportunity = request.opportunity
    areas_present = WorkArea.objects.filter(opportunity_id=request.opportunity.id).exists()
    show_area_btn = not (cache.get(get_import_area_cache_key(opportunity.id)) is not None or areas_present)
    work_area_groups_present = WorkAreaGroup.objects.filter(opportunity_id=opportunity.id).exists()
    show_workarea_groups_btn = areas_present and not work_area_groups_present

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

    status_meta = {
        status.value: {
            "label": status.label,
            "class": WORK_AREA_STATUS_COLORS.get(status),
        }
        for status in WorkAreaStatus
    }

    filterset = WorkAreaMapFilterSet(
        data=request.GET,
        opportunity=opportunity,
    )
    return render(
        request,
        template_name="microplanning/home.html",
        context={
            "show_area_btn": show_area_btn,
            "show_workarea_groups_btn": show_workarea_groups_btn,
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
            "filter_form": filterset.form,
        },
    )


def get_metrics_for_microplanning(opportunity):
    return [
        {
            "name": _("Days Remaining"),
            "value": max((opportunity.end_date - localdate()).days, 0) if opportunity.end_date else "--",
        },
    ]


@method_decorator([org_admin_required, opportunity_required, require_flag_for_opp(MICROPLANNING)], name="dispatch")
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
@require_flag_for_opp(MICROPLANNING)
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
            assignee_name=F("work_area_group__opportunity_access__user__name"),
        )
        return WorkAreaMapFilterSet(self.filter_params, queryset=qs, opportunity=self.opportunity).qs


@method_decorator([org_admin_required, opportunity_required, require_flag_for_opp(MICROPLANNING)], name="dispatch")
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
    tile_fields = ()
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
            .values("location_point")
        )


@method_decorator([org_admin_required, opportunity_required, require_flag_for_opp(MICROPLANNING)], name="dispatch")
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
@require_flag_for_opp(MICROPLANNING)
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
def cluster_work_areas(request, org_slug, opp_id):
    redirect_url = reverse(
        "microplanning:microplanning_home",
        kwargs={"org_slug": org_slug, "opp_id": opp_id},
    )

    if not WorkArea.objects.filter(opportunity_id=request.opportunity.id).exists():
        messages.error(request, _("Please upload Work Areas for this opportunity."))
        return HttpResponse(headers={"HX-Redirect": redirect_url})

    if WorkAreaGroup.objects.filter(opportunity_id=request.opportunity.id).exists():
        messages.error(request, _("Work Area Groups already exist for this opportunity."))
        return HttpResponse(headers={"HX-Redirect": redirect_url})

    lock_key = get_cluster_area_cache_lock_key(request.opportunity.id)
    if cache.lock(lock_key).locked():
        messages.error(request, _("Work Area Clustering is already in progress for this opportunity."))
        return HttpResponse(headers={"HX-Redirect": redirect_url})

    task = cluster_work_areas_task.delay(request.opportunity.id)
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


@require_GET
@org_admin_required
@opportunity_required
@require_flag_for_opp(MICROPLANNING)
def download_work_areas(request, org_slug, opp_id):
    opportunity = request.opportunity
    filterset = WorkAreaMapFilterSet(
        request.GET, queryset=WorkArea.objects.filter(opportunity=opportunity), opportunity=opportunity
    )
    queryset = filterset.qs.annotate(group_name=F("work_area_group__name"))
    response = StreamingHttpResponse(WorkAreaCSVExporter.rows(queryset), content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="work_area_summary_{opportunity.opportunity_id}.csv"'
    return response


@method_decorator([org_admin_required, opportunity_required, require_flag_for_opp(MICROPLANNING)], name="dispatch")
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
                if (
                    form.has_changed()
                    and work_area.work_area_group
                    and work_area.work_area_group.opportunity_access_id
                ):
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
