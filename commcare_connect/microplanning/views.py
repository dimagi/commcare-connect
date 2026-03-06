import csv
import uuid
from http import HTTPStatus

from celery.result import AsyncResult
from django.conf import settings
from django.contrib import messages
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.safestring import mark_safe
from django.utils.timezone import localdate
from django.utils.translation import gettext as _
from django.views import View
from django.views.decorators.http import require_GET, require_POST

from commcare_connect.flags.decorators import require_flag_for_opp
from commcare_connect.flags.flag_names import MICROPLANNING
from commcare_connect.microplanning.models import WorkArea, WorkAreaGroup
from commcare_connect.organization.decorators import opportunity_required, org_admin_required
from commcare_connect.utils.celery import CELERY_TASK_FAILURE, CELERY_TASK_SUCCESS
from commcare_connect.utils.file import get_file_extension

from .tasks import (
    WorkAreaCSVImporter,
    cluster_work_areas_task,
    get_cluster_area_cache_lock_key,
    get_import_area_cache_key,
    import_work_areas_task,
)


@require_GET
@org_admin_required
@opportunity_required
@require_flag_for_opp(MICROPLANNING)
def microplanning_home(request, *args, **kwargs):
    opportunity = request.opportunity
    areas_present = WorkArea.objects.filter(opportunity_id=request.opportunity.id).exists()
    show_area_btn = not (cache.get(get_import_area_cache_key(opportunity.id)) is not None or areas_present)
    work_area_groups_present = WorkAreaGroup.objects.filter(opportunity_id=opportunity.id).exists()
    clustering_lock = cache.lock(get_cluster_area_cache_lock_key(opportunity.id))
    show_workarea_groups_btn = areas_present and not (clustering_lock.locked() or work_area_groups_present)
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

    status_url = reverse("microplanning:clustering_status", args=(org_slug, opp_id))
    status_url += f"?clustering_task_id={task.id}"
    html = f"""
        <button type="button" disabled class="button button-md primary-dark">{_("Create Groups")}</button>
        <div id='clustering-status' class='mt-4 py-3 px-8 bg-gray-200 flex gap-4 items-center'
            hx-get="{status_url}" hx-trigger="every 2s" hx-swap="outerHTML" hx-swap-oob="outerHTML:#clustering-status">
            <div class="animate-spin rounded-full h-4 w-4 border-4 border-brand-mango border-t-transparent"></div>
            <span>{_("Work Area Clustering in progress.")}</span>
        </div>
    """
    return HttpResponse(mark_safe(html), headers={"HX-Push-Url": redirect_url})


@org_admin_required
@opportunity_required
@require_flag_for_opp(MICROPLANNING)
def clustering_status(request, org_slug, opp_id):
    task_id = request.GET.get("clustering_task_id", None)

    if task_id:
        try:
            uuid.UUID(task_id)
        except (ValueError, TypeError):
            return redirect("microplanning:microplanning_home", org_slug, opp_id)

        task = AsyncResult(task_id)
        task_meta = task._get_task_meta()
        status = task_meta.get("status")
        message = None
        icon = None

        if status == CELERY_TASK_SUCCESS:
            message = _("Work Area Clustering was successful. You may close this window.")
            icon = "fa-solid fa-circle-check text-green-600"
        elif status == CELERY_TASK_FAILURE:
            message = _("There was an error. Please try again.")
            icon = "fa-solid fa-circle-exclamation text-red-600"
        else:
            # htmx does not swap content when status 204 is returned.
            # This keeps the progress bar intact, once any of the above
            # status are triggered, the progress bar is replaced with a
            # non-refreshing div to show final status.
            return HttpResponse(status=HTTPStatus.NO_CONTENT)

        html = (
            '<div id="clustering-status" class="mt-4 py-3 px-8 bg-gray-200 flex gap-4 items-center">'
            f"<i class='fa-solid {icon} text-xl'></i><span>{message}</span>"
            "</div>"
        )
        return HttpResponse(mark_safe(html), status=HTTPStatus.OK)

    redirect_url = reverse("microplanning:microplanning_home", args=(org_slug, opp_id))
    return HttpResponse(headers={"HX-Redirect": redirect_url})
