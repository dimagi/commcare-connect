from django.conf import settings
from django.db import DEFAULT_DB_ALIAS


class SecondaryDatabaseRouter:
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
        elif db == settings.SECONDARY_DB_ALIAS:
            # Data migrations using RunPython don't need to be
            #   applied on secondary DB as they are replicated
            #   at the database level.
            operation = hints.get("operation", None)
            if operation is None:
                return True

            # Allow schema-only operations
            from django.db.migrations.operations import (
                AddField,
                AlterField,
                AlterIndexTogether,
                AlterModelOptions,
                AlterModelTable,
                AlterUniqueTogether,
                CreateModel,
                DeleteModel,
                RemoveField,
                RenameField,
            )

            ALLOWED_OPERATIONS = (
                CreateModel,
                DeleteModel,
                AlterModelTable,
                AlterModelOptions,
                AlterUniqueTogether,
                AlterIndexTogether,
                AddField,
                RemoveField,
                AlterField,
                RenameField,
            )

            if not isinstance(operation, ALLOWED_OPERATIONS):
                return False

        return True
