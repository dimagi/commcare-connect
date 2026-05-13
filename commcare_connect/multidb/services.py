import logging

import psycopg2
from django.apps import apps
from django.conf import settings
from django.db import DEFAULT_DB_ALIAS, connections, transaction
from django.utils import timezone
from psycopg2 import sql

from commcare_connect.multidb.constants import PUBLICATION_NAME, SUBSCRIPTION_NAME
from commcare_connect.multidb.models import SupersetReplicatedTable

logger = logging.getLogger(__name__)


class ReplicationSyncError(Exception):
    pass


def get_model_label_choices():
    choices = []
    for app_config in apps.get_app_configs():
        if app_config.name not in settings.LOCAL_APPS:
            continue
        for model in app_config.get_models():
            label = f"{app_config.label}.{model._meta.model_name}"
            display = f"{app_config.label}.{model.__name__} ({model._meta.db_table})"
            choices.append((label, display))
    return sorted(choices, key=lambda c: c[1])


def get_replicated_table_names():
    table_names = []
    for entry in SupersetReplicatedTable.objects.all():
        try:
            table_names.append(entry.db_table)
        except LookupError:
            logger.warning("Skipping invalid SupersetReplicatedTable entry: %s", entry.model_label)
    return sorted(table_names)


def alter_publication_on_primary(table_names):
    if not table_names:
        raise ReplicationSyncError("Refusing to set publication with an empty table list.")

    conn = connections[DEFAULT_DB_ALIAS]
    with transaction.atomic():
        with conn.cursor() as cursor:
            cursor.execute("SELECT pubname FROM pg_publication WHERE pubname = %s;", [PUBLICATION_NAME])
            if not cursor.fetchone():
                raise ReplicationSyncError(
                    f"Publication '{PUBLICATION_NAME}' does not exist on the primary. "
                    f"Run `manage.py setup_logical_replication` to create it."
                )
            tables_sql = sql.SQL(", ").join(sql.Identifier(t) for t in table_names)
            cursor.execute(
                sql.SQL("ALTER PUBLICATION {} SET TABLE {};").format(sql.Identifier(PUBLICATION_NAME), tables_sql)
            )


def refresh_subscription_on_secondary(username=None, password=None):
    if not settings.SECONDARY_DB_ALIAS:
        raise ReplicationSyncError("Secondary database is not configured.")

    if username is None:
        username = settings.SECONDARY_DB_SUPERUSER_NAME
    if password is None:
        password = settings.SECONDARY_DB_SUPERUSER_PASSWORD
    if not username or not password:
        raise ReplicationSyncError(
            "SECONDARY_DB_SUPERUSER_NAME and SECONDARY_DB_SUPERUSER_PASSWORD must be set "
            "to refresh the subscription from Django admin."
        )

    secondary_settings = connections[settings.SECONDARY_DB_ALIAS].settings_dict
    try:
        conn = psycopg2.connect(
            host=secondary_settings["HOST"],
            port=secondary_settings["PORT"],
            dbname=secondary_settings["NAME"],
            user=username,
            password=password,
            connect_timeout=10,
        )
        conn.autocommit = True
    except psycopg2.Error as e:
        raise ReplicationSyncError(f"Could not connect to secondary database: {e}")

    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT subname FROM pg_subscription WHERE subname = %s;", [SUBSCRIPTION_NAME])
            if not cursor.fetchone():
                raise ReplicationSyncError(
                    f"Subscription '{SUBSCRIPTION_NAME}' does not exist on the secondary. "
                    f"Run `manage.py setup_logical_replication` to create it."
                )
            cursor.execute(
                sql.SQL("ALTER SUBSCRIPTION {} REFRESH PUBLICATION;").format(sql.Identifier(SUBSCRIPTION_NAME))
            )
    except psycopg2.Error as e:
        raise ReplicationSyncError(f"ALTER SUBSCRIPTION REFRESH PUBLICATION failed: {e}")
    finally:
        conn.close()


def sync_replicated_tables():
    table_names = get_replicated_table_names()
    errors = []

    try:
        alter_publication_on_primary(table_names)
        primary_synced = True
    except Exception as e:
        logger.exception("Failed to ALTER PUBLICATION on primary")
        errors.append(f"Primary publication update failed: {e}")
        primary_synced = False

    try:
        refresh_subscription_on_secondary()
        secondary_synced = True
    except Exception as e:
        logger.exception("Failed to REFRESH SUBSCRIPTION on secondary")
        errors.append(f"Secondary subscription refresh failed: {e}")
        secondary_synced = False

    error_text = "\n".join(errors)
    if primary_synced and secondary_synced:
        SupersetReplicatedTable.objects.update(config_synced_at=timezone.now(), config_sync_error="")
    else:
        SupersetReplicatedTable.objects.update(config_sync_error=error_text)

    return SyncResult(primary_synced=primary_synced, secondary_synced=secondary_synced, errors=errors)


class SyncResult:
    def __init__(self, primary_synced, secondary_synced, errors):
        self.primary_synced = primary_synced
        self.secondary_synced = secondary_synced
        self.errors = errors

    @property
    def ok(self):
        return self.primary_synced and self.secondary_synced
