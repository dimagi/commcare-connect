from django.db import models
from django.utils.translation import gettext_lazy as _

from commcare_connect.opportunity.models import DeliveryType, Opportunity
from commcare_connect.organization.models import Organization
from commcare_connect.utils.db import BaseModel, slugify_uniquely


class Program(BaseModel):
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True)
    description = models.CharField(max_length=255)
    delivery_type = models.ForeignKey(DeliveryType, on_delete=models.PROTECT)
    budget = models.PositiveBigIntegerField()
    currency = models.CharField(max_length=3)
    start_date = models.DateField()
    end_date = models.DateField()
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT)

    def save(self, *args, **kwargs):
        if not self.id:
            self.slug = slugify_uniquely(self.name, self.__class__)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.slug


class ManagedOpportunity(Opportunity):
    program = models.ForeignKey(Program, on_delete=models.DO_NOTHING)
    claimed = models.BooleanField(default=False)
    org_pay_per_visit = models.IntegerField(null=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.managed = True


class ProgramApplicationStatus(models.TextChoices):
    INVITED = "invited", _("Invited")
    APPLIED = "applied", _("Applied")
    ACCEPTED = "accepted", _("Accepted")
    REJECTED = "rejected", _("Rejected")
    DECLINED = "declined", _("Declined")


class ProgramApplication(BaseModel):
    program = models.ForeignKey(Program, on_delete=models.CASCADE)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE)
    status = models.CharField(
        max_length=20,
        choices=ProgramApplicationStatus.choices,
        default=ProgramApplicationStatus.INVITED,
    )
