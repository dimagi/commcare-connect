"""Payment units and the deliver units that count towards them."""

from uuid import uuid4

from django.core.validators import MinValueValidator
from django.db import IntegrityError, models, transaction
from django.db.models import Exists, Q

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
        """``for_opportunity`` plus the given units, which an existing rule may still point at.

        Before CCCT-3057 split units by opportunity, a rule could target a unit that the 0155
        backfill gave to another opportunity. Editing the rule must not drop or reject it.
        """
        return self.filter(
            Q(opportunity=opportunity, app_id=opportunity.deliver_app_id) | Q(pk__in=[pk for pk in unit_ids if pk])
        )

    def assignable(self, opportunity, payment_unit=None):
        """The opportunity's own units that none of its other payment units count."""
        # A unit can still carry another opportunity's payment unit from before units belonged to an
        # opportunity (CCCT-3057), so only this opportunity's payment units make a unit unavailable
        available = Q(payment_unit__isnull=True) | ~Q(payment_unit__opportunity=opportunity)
        if payment_unit:
            available |= Q(payment_unit=payment_unit)
        return self.for_opportunity(opportunity).filter(available)

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
        self._adopt_unit_with_no_opportunity(opportunity, slug)
        return self.get_or_create(**lookup, defaults=defaults)

    def _adopt_unit_with_no_opportunity(self, opportunity, slug):
        """Give the opportunity one matching unit that has no opportunity, if there is one.

        Such units were created before DeliverUnit.opportunity existed or by code still running while
        it was deployed, or were left by a deleted opportunity. Only one is adopted: several can match,
        and giving them all the same opportunity would break the unique constraint.
        """
        orphan = (
            self.filter(app_id=opportunity.deliver_app_id, slug=slug, opportunity__isnull=True)
            .order_by("pk")
            .values_list("pk", flat=True)
            .first()
        )
        if not orphan:
            return
        # Both conditions are re-checked by the UPDATE itself, so a concurrent caller that already
        # adopted this unit, or another one for the same slug, wins and this call adopts nothing
        already_has_unit = self.filter(opportunity=opportunity, app_id=opportunity.deliver_app_id, slug=slug)
        try:
            with transaction.atomic():
                self.filter(pk=orphan, opportunity__isnull=True).filter(~Exists(already_has_unit)).update(
                    opportunity=opportunity
                )
        except IntegrityError:
            # A unit for the slug was inserted and committed while this UPDATE ran, which the
            # NOT EXISTS above can't see. get_or_create then returns that unit.
            pass


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
