"""Payment units and the deliver units that count towards them."""

from uuid import uuid4

from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q

from commcare_connect.opportunity.models.commcare_app import CommCareApp
from commcare_connect.opportunity.models.opportunity import Opportunity


class PaymentUnit(models.Model):
    payment_unit_id = models.UUIDField(editable=False, default=uuid4, unique=True)
    opportunity = models.ForeignKey(Opportunity, on_delete=models.PROTECT)
    amount = models.PositiveIntegerField()
    org_amount = models.PositiveIntegerField(default=0)
    name = models.CharField(max_length=255)
    # Shown to workers in the mobile app in place of the internal name, when set
    display_name = models.CharField(max_length=30, blank=True, default="")
    description = models.TextField()
    max_total = models.IntegerField(null=True)
    # None means the payment unit has no daily limit
    max_daily = models.IntegerField(null=True, blank=True, validators=[MinValueValidator(1)])
    parent_payment_unit = models.ForeignKey(
        "self",
        on_delete=models.DO_NOTHING,
        related_name="child_payment_units",
        blank=True,
        null=True,
    )
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)

    def __str__(self):
        return self.name

    def has_payable_visits(self):
        from commcare_connect.opportunity.models.visits import VisitValidationStatus

        # Trial visits get no completed work, so they are never paid. Over-limit ones can still be approved.
        return self._visits().exclude(status=VisitValidationStatus.trial).exists()

    def _visits(self):
        from commcare_connect.opportunity.models.visits import UserVisit

        # Completed work keeps paid visits with this unit after their deliver unit is removed from it, and
        # the opportunity filter drops visits a deliver unit brought from another opportunity's unit
        return UserVisit.objects.filter(
            Q(deliver_unit__payment_unit=self) | Q(completed_work__payment_unit=self),
            opportunity_id=self.opportunity_id,
        )


class DeliverUnit(models.Model):
    app = models.ForeignKey(
        CommCareApp,
        on_delete=models.CASCADE,
        related_name="deliver_units",
    )
    slug = models.SlugField(max_length=100)
    name = models.CharField(max_length=255)
    # Shown to workers in the mobile app in place of the internal name, when set
    display_name = models.CharField(max_length=30, blank=True, default="")
    payment_unit = models.ForeignKey(
        PaymentUnit,
        on_delete=models.DO_NOTHING,
        related_name="deliver_units",
        related_query_name="deliver_unit",
        null=True,
    )
    optional = models.BooleanField(default=False)

    def __str__(self):
        return self.name
