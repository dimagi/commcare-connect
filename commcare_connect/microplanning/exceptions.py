class ImportRejected(Exception):
    """Raised inside run()'s transaction to undo everything saved so far."""
