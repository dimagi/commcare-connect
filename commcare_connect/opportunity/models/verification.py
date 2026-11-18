"""Per-opportunity rules for verifying and flagging visits."""

import re
from uuid import uuid4

from django.db import models
from jsonpath_ng.ext import parse

from commcare_connect.opportunity.models.opportunity import Opportunity
from commcare_connect.opportunity.models.payment_units import DeliverUnit
from commcare_connect.utils.db import slugify_uniquely

_PLAIN_JSONPATH_FIELD = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def normalize_question_path(question_path):
    """Convert a stored question path into a JsonPath expression rooted at the form JSON.

    Accepts:
    * JsonPath expressions, with or without the leading ``$.`` (``form.value``, ``$.form.value``)
    * CommCare/XPath style paths (``/data/group/question`` or ``data/question``), where the
      ``data`` root maps to the ``form`` key of the submitted form JSON.
    """
    path = (question_path or "").strip()
    if path.startswith("$"):
        return path
    if "/" in path:
        segments = [segment for segment in path.split("/") if segment]
        if segments and segments[0] == "data":
            segments[0] = "form"
        return "$." + ".".join(
            segment if _PLAIN_JSONPATH_FIELD.match(segment) else '"{}"'.format(segment.replace('"', '\\"'))
            for segment in segments
        )
    return f"$.{path}"


def parse_question_path(question_path):
    """Return a compiled JsonPath for ``question_path``.

    Raises ``jsonpath_ng.exceptions.JSONPathError`` if the path cannot be parsed.
    """
    return parse(normalize_question_path(question_path))


class OpportunityVerificationFlags(models.Model):
    opportunity = models.OneToOneField(Opportunity, on_delete=models.CASCADE)
    duration = models.PositiveIntegerField(default=1)
    gps = models.BooleanField(default=False)
    duplicate = models.BooleanField(default=False)
    location = models.PositiveIntegerField(default=0)
    form_submission_start = models.TimeField(null=True, blank=True)
    form_submission_end = models.TimeField(null=True, blank=True)


class FormJsonValidationRules(models.Model):
    form_json_validation_rules_id = models.UUIDField(editable=False, default=uuid4, unique=True)
    slug = models.SlugField()
    name = models.CharField(max_length=25)
    deliver_unit = models.ManyToManyField(DeliverUnit)
    opportunity = models.ForeignKey(Opportunity, on_delete=models.CASCADE)
    question_path = models.CharField(max_length=255)
    question_value = models.CharField(max_length=255)

    def save(self, *args, **kwargs):
        if not self.id:
            self.slug = slugify_uniquely(self.name, self.__class__)
        super().save(*args, **kwargs)

    def get_json_path(self):
        return parse_question_path(self.question_path)


class DeliverUnitFlagRules(models.Model):
    deliver_unit = models.ForeignKey(DeliverUnit, on_delete=models.CASCADE)
    opportunity = models.ForeignKey(Opportunity, on_delete=models.CASCADE)
    check_attachments = models.BooleanField(default=False)
    duration = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = ("deliver_unit", "opportunity")
