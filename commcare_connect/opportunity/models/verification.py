"""Per-opportunity rules for verifying and flagging visits."""

from functools import lru_cache
from uuid import uuid4

from django.db import models
from jsonpath_ng.exceptions import JSONPathError
from jsonpath_ng.ext import parse
from jsonpath_ng.jsonpath import Child, Fields, Root

from commcare_connect.opportunity.models.opportunity import Opportunity
from commcare_connect.opportunity.models.payment_units import DeliverUnit
from commcare_connect.utils.db import slugify_uniquely


def _build_slash_path(question_path):
    """Build a JsonPath from a CommCare/XPath style path such as ``/data/group/question``.

    The path is assembled from jsonpath_ng nodes rather than an expression string, so segment
    names are matched literally and never need quoting or escaping. The ``data`` root maps to the
    ``form`` key of the submitted form JSON.
    """
    segments = [segment for segment in question_path.split("/") if segment]
    if not segments:
        raise JSONPathError(f"Invalid question path: {question_path!r}")
    if segments[0] == "data":
        segments[0] = "form"
    json_path = Root()
    for segment in segments:
        json_path = Child(json_path, Fields(segment))
    return json_path


@lru_cache(maxsize=512)
def parse_question_path(question_path):
    """Return a compiled JsonPath for ``question_path``, rooted at the submitted form JSON.

    Accepts:
    * JsonPath expressions, with or without the leading ``$.`` (``form.value``, ``$.form.value``)
    * CommCare/XPath style paths (``/data/group/question`` or ``data/question``)

    Compiled paths are cached because the same rules are evaluated on every form submission.
    Raises ``jsonpath_ng.exceptions.JSONPathError`` if the path cannot be parsed.
    """
    path = (question_path or "").strip()
    if path.startswith("$"):
        return parse(path)
    if "/" in path:
        return _build_slash_path(path)
    return parse(f"$.{path}")


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
