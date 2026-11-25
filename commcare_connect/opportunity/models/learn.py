"""Learn app modules and the progress workers make through them."""

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
    time_estimate = models.IntegerField(help_text="Estimated hours to complete the module")

    def __str__(self):
        return self.name


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
