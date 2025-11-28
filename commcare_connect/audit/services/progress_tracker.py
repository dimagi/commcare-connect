"""Progress tracking for long-running audit operations"""
import uuid
from typing import Any

from django.core.cache import cache


class ProgressTracker:
    """Track progress of long-running operations using Django cache"""

    def __init__(self, task_id: str | None = None, timeout: int = 3600):
        """
        Initialize progress tracker

        Args:
            task_id: Unique identifier for this task (generates one if not provided)
            timeout: Cache timeout in seconds (default 1 hour)
        """
        self.task_id = task_id or str(uuid.uuid4())
        self.timeout = timeout
        self.cache_key = f"audit_progress_{self.task_id}"
        self.steps = []  # Track individual steps

    def update(
        self,
        current: int,
        total: int,
        message: str,
        stage: str = "processing",
        step_name: str | None = None,
        extra_data: dict | None = None,
    ):
        """
        Update progress information

        Args:
            current: Current progress count
            total: Total items to process
            message: Human-readable progress message
            stage: Current stage (e.g., 'loading', 'processing', 'downloading')
            step_name: Name of the current step (for multi-step tracking)
            extra_data: Any additional data to include
        """
        percentage = round((current / total * 100) if total > 0 else 0, 1)

        # Update or add step
        if step_name:
            # Find existing step or create new one
            step_found = False
            for step in self.steps:
                if step["name"] == step_name:
                    step["current"] = current
                    step["total"] = total
                    step["percentage"] = percentage
                    step["message"] = message
                    step["status"] = "in_progress"
                    step_found = True
                    break

            if not step_found:
                self.steps.append(
                    {
                        "name": step_name,
                        "current": current,
                        "total": total,
                        "percentage": percentage,
                        "message": message,
                        "status": "in_progress",
                    }
                )

        progress_data = {
            "task_id": self.task_id,
            "current": current,
            "total": total,
            "percentage": percentage,
            "message": message,
            "stage": stage,
            "cancelled": False,
            "steps": self.steps,
        }

        if extra_data:
            progress_data.update(extra_data)

        cache.set(self.cache_key, progress_data, timeout=self.timeout)

    def update_step(self, step_name: str, percentage: int, status: str, message: str):
        """Update a specific step's progress"""
        step_found = False
        for step in self.steps:
            if step["name"] == step_name:
                step["percentage"] = percentage
                step["status"] = status
                step["message"] = message
                step_found = True
                break

        if not step_found:
            # Add new step if not found
            self.steps.append({"name": step_name, "percentage": percentage, "status": status, "message": message})

        # Save to cache
        data = self.get_progress() or {}
        data["steps"] = self.steps
        cache.set(self.cache_key, data, timeout=self.timeout)

    def complete_step(self, step_name: str, message: str = "Complete"):
        """Mark a specific step as complete"""
        self.update_step(step_name, 100, "complete", message)

    def complete(self, message: str = "Complete", result_data: dict | None = None):
        """Mark the task as complete"""
        progress_data = {
            "task_id": self.task_id,
            "current": 100,
            "total": 100,
            "percentage": 100,
            "message": message,
            "stage": "complete",
            "cancelled": False,
        }

        if result_data:
            progress_data["result"] = result_data

        cache.set(self.cache_key, progress_data, timeout=self.timeout)

    def error(self, message: str, error_details: str | None = None):
        """Mark the task as failed"""
        print(f"[PROGRESS TRACKER ERROR] Task {self.task_id}: {message}")
        if error_details:
            print(f"[ERROR DETAILS]\n{error_details}")

        progress_data = {
            "task_id": self.task_id,
            "stage": "error",
            "message": message,
            "cancelled": False,
        }

        if error_details:
            progress_data["error_details"] = error_details

        cache.set(self.cache_key, progress_data, timeout=self.timeout)

    def cancel(self):
        """Mark the task as cancelled"""
        current_data = self.get_progress() or {}
        current_data["cancelled"] = True
        current_data["stage"] = "cancelled"
        cache.set(self.cache_key, current_data, timeout=self.timeout)

    def is_cancelled(self) -> bool:
        """Check if the task has been cancelled"""
        data = self.get_progress()
        return data.get("cancelled", False) if data else False

    def get_progress(self) -> dict[str, Any] | None:
        """Get current progress data"""
        return cache.get(self.cache_key)

    def cleanup(self):
        """Remove progress data from cache"""
        cache.delete(self.cache_key)
