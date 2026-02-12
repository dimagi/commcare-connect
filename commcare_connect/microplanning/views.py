import csv
import uuid
from io import BytesIO, StringIO

from celery.result import AsyncResult
from django.conf import settings
from django.contrib import messages
from django.core.cache import cache
from django.http import FileResponse, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.translation import gettext as _
from django.views import View
from django.views.decorators.http import require_GET

from commcare_connect.flags.decorators import require_flag_for_opp
from commcare_connect.flags.flag_names import MICROPLANNING
from commcare_connect.microplanning.models import WorkAreaStatus
from commcare_connect.organization.decorators import opportunity_required, org_admin_required
from commcare_connect.utils.file import get_file_extension

from .tasks import WorkAreaCSVImporter, get_import_area_cache_key, import_work_areas_task


@require_GET
@org_admin_required
@opportunity_required
@require_flag_for_opp(MICROPLANNING)
def microplanning_home(request, *args, **kwargs):
    # cache.delete(get_import_area_cache_key(request.opportunity.id))
    return render(
        request,
        template_name="microplanning/home.html",
        context={
            "mapbox_api_key": settings.MAPBOX_TOKEN,
            "task_id": request.GET.get("task_id"),
        },
    )


@method_decorator([org_admin_required, opportunity_required, require_flag_for_opp(MICROPLANNING)], name="dispatch")
class WorkAreaImport(View):
    def get(self, request, *args, **kwargs):
        output = StringIO()
        writer = csv.writer(output)
        writer.writerow(WorkAreaCSVImporter.REQUIRED_HEADERS)
        writer.writerow(
            [
                "Sample Work Area",
                "XI1",
                "Demo Ward",
                "POINT(1 1)",
                "POLYGON((0 0,0 1,1 1,1 0,0 0))",
                10,
                12,
                WorkAreaStatus.NOT_STARTED,
            ]
        )

        csv_bytes = output.getvalue().encode("utf-8")
        bytes_io = BytesIO(csv_bytes)
        bytes_io.seek(0)
        return FileResponse(
            bytes_io,
            as_attachment=True,
            filename="work_area_template.csv",
            content_type="text/csv",
        )

    def post(self, request, org_slug, opp_id):
        lock_key = get_import_area_cache_key(request.opportunity.id)
        redirect_url = reverse("microplanning:microplanning_home", kwargs={"org_slug": org_slug, "opp_id": opp_id})

        if cache.get(lock_key):
            messages.error(request, "An import for this opportunity is already in progress. Please try again later.")
            return redirect(redirect_url)

        csv_file = request.FILES.get("csv_file")
        if not csv_file:
            messages.error(request, "No file provided.")
            return redirect(redirect_url)

        extension = get_file_extension(csv_file)
        if extension != "csv":
            messages.error(request, f"Unsupported file format: .{extension}. Please upload a CSV file.")
            return redirect(redirect_url)

        try:
            csv_content = csv_file.read().decode("utf-8")
            task = import_work_areas_task.delay(request.opportunity.id, csv_content)
            cache.set(lock_key, task.id, timeout=1200)
            messages.success(request, "Work Area upload has been started.")
            redirect_url += f"?task_id={task.id}"
        except Exception:
            cache.delete(lock_key)
            messages.error(request, "Failed to start import")

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
        response = HttpResponse(status=204)  # Task is not ready
        if result_ready:
            response["status"] = 200
            response["HX-Trigger"] = "task-completed"
        return response

    context = {
        "is_importing": cache.get(get_import_area_cache_key(request.opportunity.id)) is not None,
        "result_data": result_data,
        "title": _("Work Area Upload Results") if result_ready else _("Upload Work Areas"),
        "result_ready": result_ready,
    }

    return render(request, "microplanning/import_work_area_modal.html", context)
