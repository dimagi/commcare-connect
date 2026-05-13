from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import models


class SupersetReplicatedTable(models.Model):
    """A Django model whose table is included in the Superset logical replication set.

    Saving or deleting a row triggers an ALTER PUBLICATION on the primary and
    ALTER SUBSCRIPTION ... REFRESH PUBLICATION on the secondary. See
    `commcare_connect.multidb.services.sync_replicated_tables`.
    """

    model_label = models.CharField(
        max_length=255,
        unique=True,
        help_text="The Django model identifier in the form 'app_label.model_name'.",
    )
    config_synced_at = models.DateTimeField(null=True, blank=True)
    config_sync_error = models.TextField(blank=True)

    class Meta:
        ordering = ["model_label"]

    def __str__(self):
        return self.model_label

    @property
    def db_table(self):
        return self.get_model()._meta.db_table

    def get_model(self):
        try:
            app_label, model_name = self.model_label.split(".", 1)
        except ValueError:
            raise LookupError(f"Invalid model_label '{self.model_label}': expected 'app_label.model_name'.")
        return apps.get_model(app_label, model_name)

    def clean(self):
        try:
            self.get_model()
        except LookupError as e:
            raise ValidationError({"model_label": str(e)})
