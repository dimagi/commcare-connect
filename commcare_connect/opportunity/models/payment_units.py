"""Payment units, their pay rate history, and the deliver units that count towards them."""

from __future__ import annotations

import datetime
from uuid import uuid4

from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.db.models import Q
from django.utils.timezone import now

from commcare_connect.opportunity.models.commcare_app import CommCareApp
from commcare_connect.opportunity.models.opportunity import Opportunity
from commcare_connect.utils.db import BaseModel

# Recorded as the author of pay rate rows the system writes
SYSTEM_USER = "system"


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

    # Every way of creating or editing a unit (form, automation API, admin, factories, seed commands) saves it here,
    # so the pay rate history follows amount/org_amount: a new unit gets its Initial Pay Rate, and a pay edit updates
    # the Active Pay Rate in place. Temporary: CCCT-3018 moves pay to the pay rate form and does this explicitly.
    def save(self, *args, **kwargs):
        adding = self._state.adding
        with transaction.atomic():
            super().save(*args, **kwargs)
            if adding:
                self.pay_rates.create(
                    flw_amount=self.amount,
                    org_amount=self.org_amount,
                    planned_for=now().date(),
                    created_by=SYSTEM_USER,
                    modified_by=SYSTEM_USER,
                )
            else:
                self._sync_active_pay_rate()

    def _sync_active_pay_rate(self):
        rate = self.current_pay_rate
        # None only for a unit created during the deploy, until backfill_pay_rates gives it a row
        if rate and (rate.flw_amount, rate.org_amount) != (self.amount, self.org_amount):
            rate.flw_amount, rate.org_amount, rate.modified_by = self.amount, self.org_amount, SYSTEM_USER
            rate.save(update_fields=["flw_amount", "org_amount", "modified_by", "date_modified"])

    def pay_rate_on(self, day: datetime.date) -> PaymentUnitRate | None:
        """The pay rate in effect on `day`: the latest live rate planned on or before it."""
        return self.pay_rates.filter(cancelled_on__isnull=True, planned_for__lte=day).order_by("-planned_for").first()

    @property
    def current_pay_rate(self):
        """The Active Pay Rate: what workers are paid today."""
        return self.pay_rate_on(now().date())


class PaymentUnitRate(BaseModel):
    """One pay rate in a payment unit's pay rate history. Rows are never deleted, except with their opportunity."""

    payment_unit = models.ForeignKey(PaymentUnit, on_delete=models.PROTECT, related_name="pay_rates")
    flw_amount = models.PositiveIntegerField()
    org_amount = models.PositiveIntegerField(default=0)
    planned_for = models.DateField()
    # User's email, like created_by and modified_by
    cancelled_by = models.CharField(max_length=255, blank=True)
    cancelled_on = models.DateTimeField(null=True, blank=True)
    # When workers were told about the change; a unit's first rate isn't a change, so it stays empty
    workers_notified_on = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["payment_unit", "planned_for"],
                condition=Q(cancelled_on__isnull=True),
                name="unique_live_pay_rate_per_date",
            ),
        ]


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
