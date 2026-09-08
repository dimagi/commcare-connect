class BuildingDataUnavailable(Exception):
    """Raised when Overture could not be read. Distinct from an area that genuinely has no buildings."""


class AreaTooLarge(Exception):
    """Raised when more grid tiles were asked for than one request may fetch."""
