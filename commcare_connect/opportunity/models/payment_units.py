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


class DeliverUnitQuerySet(models.QuerySet):
    def for_opportunity(self, opportunity):
        """The units of the opportunity's current deliver app that belong to the opportunity."""
        return self.filter(opportunity=opportunity, app_id=opportunity.deliver_app_id)

    def for_opportunity_including(self, opportunity, unit_ids):
        """``for_opportunity`` plus the given units of the opportunity, which an existing rule may still use.

        A rule set up before the opportunity changed its deliver app points at a unit of the old app.
        Editing the rule must neither reject nor drop it.
        """
        return self.filter(opportunity=opportunity).filter(
            Q(app_id=opportunity.deliver_app_id) | Q(pk__in=[pk for pk in unit_ids if pk])
        )

    def assignable(self, opportunity, payment_unit=None):
        """The opportunity's own units that none of its other payment units count."""
        available = Q(payment_unit__isnull=True)
        if payment_unit:
            available |= Q(payment_unit=payment_unit)
        return self.for_opportunity(opportunity).filter(available)

    def get_or_create_for_opportunity(self, opportunity, slug, defaults=None):
        """Find or create the opportunity's unit for ``slug``, never one belonging to another opportunity.

        When an app is reused by a new opportunity, it gets fresh units, so configuring its payment
        units can't take the previous opportunity's units away from it.
        """
        return self.get_or_create(
            opportunity=opportunity, app_id=opportunity.deliver_app_id, slug=slug, defaults=defaults
        )


class DeliverUnit(models.Model):
    opportunity = models.ForeignKey(
        Opportunity,
        on_delete=models.CASCADE,
        related_name="deliver_units",
    )
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

    objects = DeliverUnitQuerySet.as_manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["opportunity", "app", "slug"], name="unique_deliver_unit_per_opportunity"),
        ]

    def __str__(self):
        return self.name
