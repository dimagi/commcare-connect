"""Shared building blocks for the opportunity models."""

from django.db import models

# Max length for CharFields backed by a TextChoices enum.
CHOICE_FIELD_MAX_LENGTH = 50


class XFormBaseModel(models.Model):
    xform_id = models.CharField(max_length=50)
    app_build_id = models.CharField(max_length=50, null=True, blank=True)
    app_build_version = models.IntegerField(null=True, blank=True)

    class Meta:
        abstract = True
