from uuid import uuid4

import pghistory
from django.db import models
from django.utils.translation import gettext_lazy as _

from commcare_connect.opportunity.models import Country, Currency, DeliveryType
from commcare_connect.organization.models import Organization
from commcare_connect.utils.db import BaseModel, slugify_uniquely


@pghistory.track(fields=["funder"])
class Program(BaseModel):
    program_id = models.UUIDField(editable=False, default=uuid4, unique=True)
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True)
    description = models.CharField()
    delivery_type = models.ForeignKey(DeliveryType, on_delete=models.PROTECT)
    budget = models.PositiveBigIntegerField()
    currency = models.ForeignKey(Currency, on_delete=models.PROTECT, null=True)
    country = models.ForeignKey(Country, on_delete=models.PROTECT, null=True)
    start_date = models.DateField()
    end_date = models.DateField()
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT)
    funder = models.ForeignKey(
        Organization,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="funded_programs",
    )
    watchers = models.ManyToManyField(
        Organization, related_name="watched_programs", blank=True, through="ProgramWatcher"
    )

    def save(self, *args, **kwargs):
        if not self.id:
            self.slug = slugify_uniquely(self.name, self.__class__)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.slug

    @property
    def currency_code(self):
        if self.currency:
            return self.currency.code
        else:
            return None


# Insert and delete rather than the default insert and update: adding and removing a
# watcher are a row insert and a row delete, and a removal would otherwise go unrecorded.
@pghistory.track(pghistory.InsertEvent(), pghistory.DeleteEvent())
class ProgramWatcher(models.Model):
    """Explicit through model for Program.watchers.

    Declared explicitly only so its rows can be audited. Django's autodetector ignores
    auto-created through models, so pghistory generates an event model for one but never
    installs the triggers that would populate it.
    """

    program = models.ForeignKey(Program, on_delete=models.CASCADE)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE)

    class Meta:
        # Matches the table Django generated for the ManyToManyField in migration 0016, so
        # this model adopts the existing table and its rows instead of creating a new one.
        db_table = "program_program_watchers"
        unique_together = [("program", "organization")]


class ProgramApplicationStatus(models.TextChoices):
    INVITED = "invited", _("Invited")
    APPLIED = "applied", _("Applied")
    ACCEPTED = "accepted", _("Accepted")
    REJECTED = "rejected", _("Rejected")
    DECLINED = "declined", _("Declined")


class ProgramApplication(BaseModel):
    program_application_id = models.UUIDField(editable=False, default=uuid4, unique=True)
    program = models.ForeignKey(Program, on_delete=models.CASCADE)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE)
    status = models.CharField(
        max_length=20,
        choices=ProgramApplicationStatus.choices,
        default=ProgramApplicationStatus.INVITED,
    )
