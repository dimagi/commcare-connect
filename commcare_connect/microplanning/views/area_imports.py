import csv
import logging
import uuid

from celery.result import AsyncResult
from django.contrib import messages
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views import View
from django.views.decorators.http import require_POST
from waffle.decorators import waffle_flag

from commcare_connect.flags.flag_names import MICROPLANNING
from commcare_connect.microplanning.const import WORK_AREA_MAX_OVERLAP_RATIO
from commcare_connect.microplanning.models import (
    ImplementationArea,
    WorkArea,
)
from commcare_connect.microplanning.tasks import (
    ImplementationAreaCSVImporter,
    WorkAreaCSVImporter,
    get_implementation_area_import_cache_key,
    get_import_area_cache_key,
    import_implementation_areas_task,
    import_work_areas_task,
)
from commcare_connect.organization.decorators import (
    opp_admin_access_required,
    opportunity_required,
)
from commcare_connect.utils.file import get_file_extension

logger = logging.getLogger(__name__)


@method_decorator([opp_admin_access_required, opportunity_required, waffle_flag(MICROPLANNING)], name="dispatch")
class AreaImportView(View):
    """Saves an uploaded area CSV and queues its import. An opportunity runs one import at a time."""

    import_task = None
    # Takes the opportunity ID, returns the cache key that marks its import as running.
    get_lock_key = None
    file_prefix = ""
    started_message = ""
    # Added to the redirect, so the page polls the matching status endpoint.
    status_query = ""

    def post(self, request, org_slug, opp_id):
        redirect_url = reverse(
            "microplanning:microplanning_home",
            kwargs={"org_slug": org_slug, "opp_id": opp_id},
        )
        error = self.get_upload_error(request)
        if error:
            messages.error(request, error)
            return redirect(redirect_url)

        opportunity_id = request.opportunity.id
        lock_key = self.get_lock_key(opportunity_id)
        # Taken before the task is queued, because the task clears it when it ends. add() lets only one
        # of two uploads sent at the same moment through.
        if not cache.add(lock_key, "running", timeout=1200):
            messages.error(request, _("An import for this opportunity is already in progress."))
            return redirect(redirect_url)
        file_name = f"{self.file_prefix}-{opportunity_id}-{uuid.uuid4().hex}.csv"
        saved_name = None
        try:
            saved_name = default_storage.save(file_name, ContentFile(request.FILES["csv_file"].read()))
            task = self.import_task.delay(opp_id=opportunity_id, file_name=saved_name)
        except Exception:
            cache.delete(lock_key)
            if saved_name:
                _delete_unqueued_upload(saved_name)
            raise
        messages.info(request, self.started_message)
        return redirect(f"{redirect_url}?task_id={task.id}{self.status_query}")

    def get_upload_error(self, request):
        """Why this upload can't start, or None."""
        csv_file = request.FILES.get("csv_file")
        if not csv_file or get_file_extension(csv_file).lower() != "csv":
            return _("Unsupported file format. Please upload a CSV file.")
        return None


def _delete_unqueued_upload(file_name):
    """Delete an upload whose import was never queued. A failure is logged, so the queuing error is the one raised."""
    try:
        default_storage.delete(file_name)
    except Exception:
        logger.exception("Failed to delete unqueued upload %s", file_name)


class WorkAreaImport(AreaImportView):
    import_task = import_work_areas_task
    get_lock_key = staticmethod(get_import_area_cache_key)
    file_prefix = "work_area_upload"
    started_message = gettext_lazy("Work Area upload has been started.")

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


class ImplementationAreaImport(AreaImportView):
    import_task = import_implementation_areas_task
    get_lock_key = staticmethod(get_implementation_area_import_cache_key)
    file_prefix = "implementation_area_upload"
    started_message = gettext_lazy("Implementation Area upload has been started.")
    status_query = "&area_type=implementation_area"

    def get(self, request, *args, **kwargs):
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="implementation_area_template.csv"'
        writer = csv.writer(response)
        writer.writerow(ImplementationAreaCSVImporter.HEADERS.values())
        writer.writerow(["Ward North", "77.1 28.6", "POLYGON((77 28,78 28,78 29,77 29,77 28))"])
        return response

    def get_upload_error(self, request):
        if ImplementationArea.objects.filter(opportunity_id=request.opportunity.id).exists():
            return _("Implementation Areas already exist for this opportunity.")
        return super().get_upload_error(request)


@require_POST
@opp_admin_access_required
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


@opp_admin_access_required
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

    adding = area_type == "work_area" and _is_adding_work_areas(
        opportunity_id=request.opportunity.id, created=(result_data or {}).get("created", 0)
    )
    context = {
        "result_ready": result_ready,
        "result_data": result_data,
        "task_id": task_id,
        **_area_modal_context(org_slug=org_slug, opp_id=opp_id, area_type=area_type, adding=adding),
    }
    return render(request, "microplanning/import_area_modal.html", context)


def _area_modal_context(org_slug, opp_id, area_type, adding=False):
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
        "modal_title": _("Add Work Areas") if adding else _("Upload Work Areas"),
        "records_label": _("Work Areas"),
        "success_noun": _("work area(s)"),
        "upload_url": reverse("microplanning:upload_work_areas", kwargs={"org_slug": org_slug, "opp_id": opp_id}),
        "status_url": reverse("microplanning:import_status", kwargs={"org_slug": org_slug, "opp_id": opp_id}),
        "column_requirements": [
            _("Area Slug – unique identifier for each work area should be unique in an opportunity"),
            _("Ward – name of the ward"),
            _("Centroid – longitude and latitude separated by space (e.g. 77.123 28.456)"),
            _("Boundary – Polygon in WKT format"),
            _(
                "Boundaries must not overlap each other or existing work areas by more than %(percent)d%% "
                "of the smaller work area"
            )
            % {"percent": round(WORK_AREA_MAX_OVERLAP_RATIO * 100)},
            _("Building Count – positive integer"),
            _("Expected Visit Count – positive integer"),
            _("LGA – name of the LGA the work area is in"),
            _("State – name of the state the work area is in"),
            _("Implementation Area – (optional) name of the matching Implementation Area"),
            _(
                "Work Area Group Name – (optional) leave all rows blank to use automatic clustering, or "
                "fill group name in all rows to skip it. Rows sharing a group name must share a Ward."
            ),
        ],
    }


def _is_adding_work_areas(opportunity_id: int, created: int) -> bool:
    """Whether an upload adds to work areas that were already there.

    ``created`` is the number of work areas the upload saved, so a first upload's own rows don't
    count. While an upload runs its rows aren't committed, so they aren't counted either.
    """
    return WorkArea.objects.filter(opportunity_id=opportunity_id).count() > created
