CELERY_TASK_SUCCESS = "SUCCESS"
CELERY_TASK_IN_PROGRESS = "PROGRESS"


def set_task_progress(task, message, is_complete=False):
    task.update_state(state=CELERY_TASK_SUCCESS if is_complete else CELERY_TASK_IN_PROGRESS, meta={"message": message})


def get_task_progress_message(task):
    if task.info and "message" in task.info:
        return task.info["message"]
