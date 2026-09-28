import time

from celery import current_app
from celery.result import AsyncResult
from django.http import FileResponse, Http404
from django.shortcuts import render
from django_tables2.export import TableExport

CELERY_TASK_SUCCESS = "SUCCESS"
CELERY_TASK_IN_PROGRESS = "PROGRESS"
CELERY_TASK_PENDING = "PENDING"
CELERY_TASK_FAILURE = "FAILURE"

# {task_id: {"scope": ..., "started": epoch seconds}}
PENDING_EXPORTS_SESSION_KEY = "pending_exports"


def set_task_progress(task, message, is_complete=False, is_error=False, errors=None):
    """`errors` is an optional {description: [row numbers]} mapping for file imports that
    validate the whole file before writing, so callers can list what stopped the import."""
    task.update_state(
        state=CELERY_TASK_SUCCESS if is_complete else CELERY_TASK_IN_PROGRESS,
        meta={"message": message, "is_error": is_error, "errors": errors or {}},
    )


def get_task_progress_message(task):
    # A failed task carries the exception in `info` rather than the progress meta, and testing
    # `"message" in <exception>` raises rather than returning False.
    info = task.info
    if isinstance(info, dict):
        return info.get("message")


def get_task_progress(request, task_id, ownership_check=None):
    """
    Build the progress dict for a Celery task, running an optional ownership check.
    ownership_check: callable(request, task_meta) -> None
        Should raise 404 / PermissionDenied if the requester may not view this task.
    """
    task = AsyncResult(task_id)
    task_meta = task._get_task_meta()
    status = task_meta.get("status")

    if ownership_check and status != CELERY_TASK_PENDING:
        ownership_check(request, task_meta)

    result = task_meta.get("result")
    progress = {
        "complete": status == CELERY_TASK_SUCCESS,
        "message": get_task_progress_message(task),
        # Set by imports that report per-row errors; see set_task_progress.
        "errors": result.get("errors") or {} if isinstance(result, dict) else {},
    }
    if status == CELERY_TASK_FAILURE:
        progress["error"] = result
    return progress


def render_export_status(
    request,
    task_id,
    download_url,
    export_status_url,
    ownership_check=None,
):
    """Generic export status renderer."""
    progress = get_task_progress(request, task_id, ownership_check)
    if progress["complete"] or progress.get("error"):
        forget_export(request, task_id)
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
    from commcare_connect.utils.storages import ExportS3Boto3Storage

    try:
        export_file = ExportS3Boto3Storage().open(saved_filename)
    except FileNotFoundError as e:
        raise Http404("Export file no longer available") from e

    return FileResponse(
        export_file,
        as_attachment=True,
        filename=f"{filename_without_ext}.{export_format}",
        content_type=TableExport.FORMATS.get(export_format),
    )


def track_export(request, scope, task_id):
    """Kept in the session, not the URL, so a refresh doesn't replay the notification.
    render_export_status forgets it once the outcome is shown."""
    pending = request.session.get(PENDING_EXPORTS_SESSION_KEY, {})
    pending[task_id] = {"scope": scope, "started": time.time()}
    request.session[PENDING_EXPORTS_SESSION_KEY] = pending


def pending_exports(request, scope):
    pending = request.session.get(PENDING_EXPORTS_SESSION_KEY, {})
    # An expired result reports as PENDING and would poll forever.
    cutoff = time.time() - current_app.conf.result_expires.total_seconds()
    live = {task_id: export for task_id, export in pending.items() if export["started"] > cutoff}
    if live != pending:
        request.session[PENDING_EXPORTS_SESSION_KEY] = live
    return [task_id for task_id, export in live.items() if export["scope"] == scope]


def forget_export(request, task_id):
    pending = request.session.get(PENDING_EXPORTS_SESSION_KEY, {})
    if pending.pop(task_id, None):
        request.session[PENDING_EXPORTS_SESSION_KEY] = pending
