import pghistory.models
from django.apps import apps
from django.conf import settings

from commcare_connect.multidb.constants import (
    PUBLICATION_NAME,
    REPLICATION_EXCLUDED_MODELS,
    REPLICATION_INCLUDED_MODELS,
)


def get_replicated_models():
    """Models whose tables should be present in the publication."""
    return REPLICATION_INCLUDED_MODELS


def get_replicated_tables() -> set[str]:
    """The set of db_table names that should be replicated."""
    return {model._meta.db_table for model in get_replicated_models()}


def _local_app_labels() -> set[str]:
    return {app_config.label for app_config in apps.get_app_configs() if app_config.name in settings.LOCAL_APPS}


def get_models_requiring_classification() -> set:
    """Local, concrete, non-history models that must appear in one list.

    Excludes pghistory Event models and auto-created m2m through tables —
    these are never replicated and never need classifying.
    """
    local_labels = _local_app_labels()
    return {
        model
        for model in apps.get_models()
        if model._meta.app_label in local_labels
        and not model._meta.auto_created
        and not issubclass(model, pghistory.models.Event)
    }


def get_unclassified_models() -> set:
    classified = set(REPLICATION_INCLUDED_MODELS) | set(REPLICATION_EXCLUDED_MODELS)
    return get_models_requiring_classification() - classified


def replication_is_active() -> bool:
    """Whether the deploy-time refresh should run in this environment."""
    return bool(settings.REPLICATION_ENABLED and settings.SECONDARY_DB_ALIAS)


def publication_exists(connection) -> bool:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pubname FROM pg_publication WHERE pubname = %s",
            [PUBLICATION_NAME],
        )
        return cursor.fetchone() is not None


def _current_publication_tables(connection) -> set[str]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT tablename FROM pg_publication_tables WHERE pubname = %s",
            [PUBLICATION_NAME],
        )
        return {row[0] for row in cursor.fetchall()}


def is_publication_in_sync(connection) -> bool:
    return get_replicated_tables() == _current_publication_tables(connection)
