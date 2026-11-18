"""Payment units and the deliver units that count towards them."""

from uuid import uuid4

from django.core.validators import MinValueValidator
from django.db import models

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


class DeliverUnitQuerySet(models.QuerySet):
    def for_opportunity(self, opportunity):
        """The units of the opportunity's current deliver app that belong to the opportunity."""
        return self.filter(opportunity=opportunity, app_id=opportunity.deliver_app_id)

    def get_or_create_for_opportunity(self, opportunity, slug, defaults=None):
        """Find or create the opportunity's unit for ``slug``, never one belonging to another opportunity.

        When an app is reused by a new opportunity, it gets fresh units, so configuring its payment
        units can't take the previous opportunity's units away from it.
        """
        lookup = {"opportunity": opportunity, "app_id": opportunity.deliver_app_id, "slug": slug}
        try:
            return self.get(**lookup), False
        except self.model.DoesNotExist:
            pass
        # Units with no opportunity were created before DeliverUnit.opportunity existed, or by code
        # still running while it was deployed. Only apps no opportunity uses have them after the backfill.
        self.filter(app_id=opportunity.deliver_app_id, slug=slug, opportunity__isnull=True).update(
            opportunity=opportunity
        )
        return self.get_or_create(**lookup, defaults=defaults)


class DeliverUnit(models.Model):
    # Nullable, and SET_NULL rather than CASCADE, until part 2 of CCCT-3057 splits the units that
    # several opportunities used: until then another opportunity's visits can still point at a unit.
    # opportunity/deletion.py still deletes units by their payment unit's opportunity, as before this
    # field existed; units it doesn't reach are left with no opportunity, like units on an unused app.
    opportunity = models.ForeignKey(
        Opportunity,
        on_delete=models.SET_NULL,
        related_name="deliver_units",
        null=True,
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
            # Units with no opportunity (NULL) don't conflict, so units on apps no opportunity uses are unaffected
            models.UniqueConstraint(fields=["opportunity", "app", "slug"], name="unique_deliver_unit_per_opportunity"),
        ]

    def __str__(self):
        return self.name
