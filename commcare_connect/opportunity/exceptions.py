class ListTooLongError(Exception):
    """Raised when a list exceeds the maximum allowed size for a bulk operation."""


class TaskAlreadyAssignedError(Exception):
    """Raised when a task type is already assigned (and not yet completed) for an opportunity access."""
