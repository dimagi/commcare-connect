"""Reference data shared across opportunities: delivery types, currencies, countries and exchange rates."""

import operator
from functools import reduce

from django.db import models
from django.db.models import Exists, OuterRef


class DeliveryType(models.Model):
    name = models.CharField(max_length=255)
    slug = models.CharField(max_length=255)
    description = models.CharField(max_length=255)

    def __str__(self):
        return self.name

    # Reverse relations to every model with a delivery_type FK. Deleting a referenced type would break
    # opportunities, be refused for programs and solicitations, and cascade-delete worker credentials.
    REFERENCING_RELATIONS = ("opportunity", "program", "solicitation", "usercredential")

    @classmethod
    def in_use_condition(cls):
        """True for types that any referencing model points at; usable in filter() and annotate()."""
        return reduce(
            operator.or_,
            (
                Exists(cls._meta.get_field(name).related_model.objects.filter(delivery_type=OuterRef("pk")))
                for name in cls.REFERENCING_RELATIONS
            ),
        )

    def is_in_use(self):
        return DeliveryType.objects.filter(DeliveryType.in_use_condition(), pk=self.pk).exists()


class Currency(models.Model):
    code = models.CharField(max_length=3, primary_key=True)  # ISO 4217
    name = models.CharField(max_length=64)

    def __str__(self):
        return f"{self.code} ({self.name})"


class Country(models.Model):
    code = models.CharField(max_length=3, primary_key=True)  # ISO 3166-1 alpha-3
    name = models.CharField(max_length=128)
    currency = models.ForeignKey(Currency, on_delete=models.SET_NULL, null=True, blank=True)

    def __str__(self):
        return self.name


class ExchangeRate(models.Model):
    currency_code = models.CharField(max_length=3)
    rate = models.DecimalField(max_digits=10, decimal_places=6)
    rate_date = models.DateField(db_index=True)
    fetched_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["currency_code", "rate_date"], name="unique_currency_code_date")
        ]

    @classmethod
    def latest_exchange_rate(cls, currency_code, date):
        from commcare_connect.opportunity.tasks import fetch_exchange_rates

        latest_rates = cls.objects.filter(currency_code=currency_code, rate_date__lte=date).order_by("-rate_date")
        if latest_rates:
            return latest_rates.first()
        else:
            date = date.replace(day=1)
            return fetch_exchange_rates(date, currency_code)
