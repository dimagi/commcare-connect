"""Free-form JSON records kept for experiments."""

from django.db import models

from commcare_connect.opportunity.models.opportunity import Opportunity
from commcare_connect.organization.models import Organization
from commcare_connect.users.models import User


class LabsRecord(models.Model):
    experiment = models.TextField()
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, null=True)
    opportunity = models.ForeignKey(Opportunity, on_delete=models.CASCADE, null=True)
    program = models.ForeignKey("program.Program", on_delete=models.CASCADE, null=True)
    labs_record = models.ForeignKey("LabsRecord", on_delete=models.CASCADE, null=True)
    type = models.CharField(max_length=255)
    data = models.JSONField()
    public = models.BooleanField(default=False)

    def __str__(self):
        return f"ExperimentRecord({self.user}, {self.organization}, {self.opportunity}, {self.experiment})"
