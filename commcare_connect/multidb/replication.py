from commcare_connect.multidb.constants import REPLICATION_INCLUDED_MODELS


def get_replicated_models():
    """Models whose tables should be present in the publication."""
    return REPLICATION_INCLUDED_MODELS


def get_replicated_tables() -> set[str]:
    """The set of db_table names that should be replicated."""
    return {model._meta.db_table for model in get_replicated_models()}
