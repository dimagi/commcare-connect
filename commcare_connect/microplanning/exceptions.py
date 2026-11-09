class ImportRejected(Exception):
    """Raised inside run()'s transaction to undo everything saved so far."""


class InaccessibilityRequestNotPendingError(Exception):
    """The work area has no inaccessibility request awaiting review."""
