import csv
import json
import uuid

from celery.result import AsyncResult
from django.conf import settings
from django.contrib import messages
from django.contrib.gis.db.models import Union as GeoUnion
from django.contrib.gis.db.models.functions import AsGeoJSON
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db.models import F
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.timezone import localdate
from django.utils.translation import gettext as _
from django.views import View
from django.views.decorators.http import require_GET

from commcare_connect.flags.decorators import require_flag_for_opp
from commcare_connect.flags.flag_names import MICROPLANNING
from commcare_connect.microplanning.models import WorkArea, WorkAreaGroup
from commcare_connect.organization.decorators import opportunity_required, org_admin_required
from commcare_connect.utils.file import get_file_extension

from .tasks import WorkAreaCSVImporter, get_import_area_cache_key, import_work_areas_task


@require_GET
@org_admin_required
@opportunity_required
@require_flag_for_opp(MICROPLANNING)
def microplanning_home(request, *args, **kwargs):
    opportunity = request.opportunity
    areas_present = WorkArea.objects.filter(opportunity_id=request.opportunity.id).exists()
    show_area_btn = not (cache.get(get_import_area_cache_key(opportunity.id)) is not None or areas_present)
    show_workarea_groups_btn = (
        areas_present and not WorkAreaGroup.objects.filter(opportunity_id=opportunity.id).exists()
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


@org_admin_required
@opportunity_required
@require_flag_for_opp(MICROPLANNING)
def workareas_geojson(request, org_slug, opp_id):
    opp_id = request.opportunity.id

    features = [
        {
            "type": "Feature",
            "geometry": json.loads(wa["geometry"]),
            "properties": {
                "id": wa["id"],
                "status": wa["status"],
                "group_id": wa["group_id"],
                "assigned_user_id": wa["assigned_user_id"],
            },
        }
        for wa in WorkArea.objects.filter(opportunity_id=opp_id)
        .annotate(geometry=AsGeoJSON("boundary", precision=4))  # ← precision=4
        .values(
            "id",
            "status",
            "geometry",
            group_id=F("work_area_group__id"),
            assigned_user_id=F("work_area_group__assigned_user_id"),
        )
        .iterator(chunk_size=2000)
    ]

    group_features = [
        {
            "type": "Feature",
            "geometry": json.loads(g["boundary_union"]),
            "properties": {"group_id": g["group_id"]},
        }
        for g in WorkArea.objects.filter(opportunity_id=opp_id, work_area_group__isnull=False)
        .values(group_id=F("work_area_group__id"))
        .annotate(boundary_union=AsGeoJSON(GeoUnion("boundary")))
    ]

    return JsonResponse(
        {
            "type": "FeatureCollection",
            "features": features,
            "group_features": group_features,
        }
    )
