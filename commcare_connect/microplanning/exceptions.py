class AreaCSVColumnsError(Exception):
    """An area CSV is missing required columns, so its rows can't be read."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class InaccessibilityRequestNotPendingError(Exception):
    """The work area has no inaccessibility request awaiting review."""
