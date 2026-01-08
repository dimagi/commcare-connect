from celery.result import AsyncResult
from django.core.files.storage import storages
from django.http import FileResponse, Http404
from django.shortcuts import render
from django_tables2.export import TableExport

CELERY_TASK_SUCCESS = "SUCCESS"
CELERY_TASK_IN_PROGRESS = "PROGRESS"


def set_task_progress(task, message, is_complete=False):
    task.update_state(state=CELERY_TASK_SUCCESS if is_complete else CELERY_TASK_IN_PROGRESS, meta={"message": message})


def get_task_progress_message(task):
    if task.info and "message" in task.info:
        return task.info["message"]


def render_export_status(
    request,
    task_id,
    download_url,
    export_status_url,
    ownership_check=None,
):
    """
    Generic export status renderer.
    ownership_check: callable(request, task_meta) -> None
        Should raise 404 / PermissionDenied if invalid.
    """

    task = AsyncResult(task_id)
    task_meta = task._get_task_meta()
    status = task_meta.get("status")

    if ownership_check:
        ownership_check(request, task_meta)

    progress = {
        "complete": status == CELERY_TASK_SUCCESS,
        "message": get_task_progress_message(task),
    }

    if status == "FAILURE":
        progress["error"] = task_meta.get("result")

    return render(
        request,
        "components/upload_progress_bar.html",
        {
            "task_id": task_id,
            "progress": progress,
            "download_url": download_url,
            "export_status_url": export_status_url,
        },
    )


def download_export_file(
    task_id,
    filename_without_ext,
):
    """
    Generic export download handler.
    """
    task = AsyncResult(task_id)

    if task.status != CELERY_TASK_SUCCESS:
        raise Http404("Export not ready")

    saved_filename = task.result
    if not saved_filename:
        raise Http404("Export file not found")

    export_format = saved_filename.split(".")[-1]
    export_file = storages["default"].open(saved_filename)

    return FileResponse(
        export_file,
        as_attachment=True,
        filename=f"{filename_without_ext}.{export_format}",
        content_type=TableExport.FORMATS.get(export_format),
    )
