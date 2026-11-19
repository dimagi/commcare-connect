"""Learn app modules and the progress workers make through them."""

import math

from django.db import models

from commcare_connect.opportunity.models.access import OpportunityAccess
from commcare_connect.opportunity.models.base import XFormBaseModel
from commcare_connect.opportunity.models.commcare_app import CommCareApp
from commcare_connect.opportunity.models.opportunity import Opportunity
from commcare_connect.users.models import User


class LearnModule(models.Model):
    app = models.ForeignKey(
        CommCareApp,
        on_delete=models.CASCADE,
        related_name="learn_modules",
    )
    slug = models.SlugField()
    name = models.CharField(max_length=255)
    description = models.TextField()
    time_estimate = models.IntegerField(
        help_text="Estimated hours to complete the module, rounded up. Kept for app versions that predate minutes"
    )
    # Null only for rows written before HQ sent minutes; read through estimated_minutes.
    time_estimate_minutes = models.PositiveIntegerField(
        null=True, blank=True, help_text="Estimated minutes to complete the module"
    )

    def __str__(self):
        return self.name

    @property
    def estimated_minutes(self) -> int:
        if self.time_estimate_minutes is not None:
            return self.time_estimate_minutes
        return self.time_estimate * 60


def resolve_time_estimate(hours, minutes) -> tuple[int | None, int | None]:
    """Return (hours, minutes) for a learn module block from HQ.

    HQ sends ``time_estimate_minutes`` alongside the legacy ``time_estimate`` hours. Forms built before
    that only have hours, so minutes fall back to hours × 60; if a form only has minutes, hours are
    derived by rounding up so older apps never show less time than the module takes.
    """
    hours = _to_int(hours)
    minutes = _to_int(minutes)
    if minutes is None:
        return hours, hours * 60 if hours is not None else None
    if hours is None:
        hours = math.ceil(minutes / 60)
    return hours, minutes


def _to_int(value) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


class CompletedModule(XFormBaseModel):
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="completed_modules",
    )
    module = models.ForeignKey(LearnModule, on_delete=models.PROTECT)
    opportunity = models.ForeignKey(Opportunity, on_delete=models.PROTECT)
    opportunity_access = models.ForeignKey(OpportunityAccess, on_delete=models.CASCADE, null=True)
    date = models.DateTimeField()
    duration = models.DurationField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["xform_id", "module", "opportunity_access"], name="unique_xform_completed_module"
            )
        ]


class Assessment(XFormBaseModel):
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="assessments",
    )
    app = models.ForeignKey(CommCareApp, on_delete=models.PROTECT)
    opportunity = models.ForeignKey(Opportunity, on_delete=models.PROTECT)
    opportunity_access = models.ForeignKey(OpportunityAccess, on_delete=models.CASCADE, null=True)
    date = models.DateTimeField()
    score = models.IntegerField()
    passing_score = models.IntegerField()
    passed = models.BooleanField()
