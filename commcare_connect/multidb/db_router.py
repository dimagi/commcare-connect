from django.conf import settings
from django.db import DEFAULT_DB_ALIAS


class ConnectDatabaseRouter:
    """
    A router to direct migration operations for specific models to the secondary database.
    """

    def db_for_read(self, model, **hints):
        # Prevent reads from the secondary database
        return DEFAULT_DB_ALIAS

    def db_for_write(self, model, **hints):
        # Prevent writes to the secondary database
        return DEFAULT_DB_ALIAS

    def allow_relation(self, obj1, obj2, **hints):
        # Allow relations only in the default database
        return None

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        if db == DEFAULT_DB_ALIAS:
            return True
        elif db and db == settings.SECONDARY_DB_ALIAS:
            if "run_on_secondary" in hints:
                return hints["run_on_secondary"]
            else:
                return True

        return True
