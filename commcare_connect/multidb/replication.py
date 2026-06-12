import pghistory.models
from django.apps import apps
from django.conf import settings

from commcare_connect.multidb.constants import REPLICATION_EXCLUDED_MODELS, REPLICATION_INCLUDED_MODELS


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
