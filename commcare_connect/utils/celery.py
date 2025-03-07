def set_task_progress(task, message, is_complete=False):
    task.update_state(state="SUCCESS" if is_complete else "PROGRESS", meta={"message": message})


def get_task_progress_message(task):
    if task.info and "message" in task.info:
        return task.info["message"]
