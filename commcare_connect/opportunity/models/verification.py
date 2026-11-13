"""Per-opportunity rules for verifying and flagging visits."""

from uuid import uuid4

from django.db import models

from commcare_connect.opportunity.models.opportunity import Opportunity
from commcare_connect.opportunity.models.payment_units import DeliverUnit
from commcare_connect.utils.db import slugify_uniquely


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


class DeliverUnitFlagRules(models.Model):
    deliver_unit = models.ForeignKey(DeliverUnit, on_delete=models.CASCADE)
    opportunity = models.ForeignKey(Opportunity, on_delete=models.CASCADE)
    check_attachments = models.BooleanField(default=False)
    duration = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = ("deliver_unit", "opportunity")
