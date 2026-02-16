import csv
import uuid

from celery.result import AsyncResult
from django.conf import settings
from django.contrib import messages
from django.core.cache import cache
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.translation import gettext as _
from django.views import View
from django.views.decorators.http import require_GET

from commcare_connect.flags.decorators import require_flag_for_opp
from commcare_connect.flags.flag_names import MICROPLANNING
from commcare_connect.microplanning.models import WorkArea
from commcare_connect.organization.decorators import opportunity_required, org_admin_required
from commcare_connect.utils.file import get_file_extension

from .tasks import WorkAreaCSVImporter, get_import_area_cache_key, import_work_areas_task


@require_GET
@org_admin_required
@opportunity_required
@require_flag_for_opp(MICROPLANNING)
def microplanning_home(request, *args, **kwargs):
    hide_import_area_button = (
        cache.get(get_import_area_cache_key(request.opportunity.id)) is not None
        or WorkArea.objects.filter(opportunity_id=request.opportunity.id).exists()
    )
    return render(
        request,
        template_name="microplanning/home.html",
        context={
            "hide_import_area_button": hide_import_area_button,
            "mapbox_api_key": settings.MAPBOX_TOKEN,
            "task_id": request.GET.get("task_id"),
        },
    )


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
                "POLYGON((1 0,0 1,1 1,1 0,0 1))",
                10,
                12,
            ]
        )
        return response

    def post(self, request, org_slug, opp_id):
        redirect_url = reverse(
            "microplanning:microplanning_home", kwargs={"org_slug": org_slug, "opp_id": request.opportunity.id}
        )

        if WorkArea.objects.filter(opportunity_id=request.opportunity.id).exists():
            messages.error(request, _("Work Areas already exist for this opportunity."))
            return redirect_url

        lock_key = get_import_area_cache_key(request.opportunity.id)

        if cache.get(lock_key):
            messages.error(request, _("An import for this opportunity is already in progress."))
            return redirect(redirect_url)

        csv_file = request.FILES.get("csv_file")
        if not csv_file:
            messages.error(request, _("No file provided."))
            return redirect(redirect_url)

        extension = get_file_extension(csv_file)
        if extension != "csv":
            messages.error(request, _(f"Unsupported file format: .{extension}. Please upload a CSV file."))
            return redirect(redirect_url)

        try:
            csv_content = csv_file.read().decode("utf-8")
            task = import_work_areas_task.delay(request.opportunity.id, csv_content)
            cache.set(lock_key, task.id, timeout=1200)
            messages.success(request, _("Work Area upload has been started."))
            redirect_url += f"?task_id={task.id}"
        except Exception:
            cache.delete(lock_key)
            messages.error(request, _("Failed to start import."))
        return redirect(redirect_url)


@org_admin_required
@opportunity_required
@require_flag_for_opp(MICROPLANNING)
def import_status(request, org_slug, opp_id):
    task_id = request.GET.get("task_id", None)
    status_check = request.GET.get("status_check", None) == "1"

    result_ready = False
    result_data = None

    if task_id:
        try:
            task_id = uuid.UUID(task_id)
            task_id = str(task_id)
        except (ValueError, TypeError):
            return HttpResponse(status=404)

        result = AsyncResult(str(task_id))
        result_ready = result.ready()
        if result_ready:
            result_data = result.result

    if status_check:
        response = HttpResponse(status=204)  # default: not ready
        if result_ready:
            triggers = ["task-completed"]
            if result_data and result_data.get("created", 0) > 1:
                triggers.append("remove-import-button")
            response.status_code = 200
            response["HX-Trigger"] = ",".join(triggers)
        return response

    context = {
        "result_ready": result_ready,
        "result_data": result_data,
        "title": _("Work Area Upload Outcome") if result_ready else _("Upload Work Areas"),
    }

    return render(request, "microplanning/import_work_area_modal.html", context)
