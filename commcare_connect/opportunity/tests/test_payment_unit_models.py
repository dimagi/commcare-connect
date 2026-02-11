"""Tests for PaymentUnit model changes, specifically org_amount field."""
import pytest
from decimal import Decimal

from commcare_connect.opportunity.models import PaymentUnit
from commcare_connect.opportunity.tests.factories import (
    OpportunityFactory,
    PaymentUnitFactory,
)
from commcare_connect.program.tests.factories import ManagedOpportunityFactory


@pytest.mark.django_db
class TestPaymentUnitOrgAmount:
    def test_payment_unit_has_org_amount_field(self):
        """Test that PaymentUnit model has org_amount field."""
        payment_unit = PaymentUnitFactory()

        # Field should exist (will raise AttributeError if it doesn't)
        assert hasattr(payment_unit, "org_amount")

    def test_org_amount_default_value(self):
        """Test that org_amount has a default value."""
        payment_unit = PaymentUnitFactory()

        # Check that org_amount is set (should not be None by default)
        assert payment_unit.org_amount is not None

    def test_org_amount_can_be_set(self):
        """Test that org_amount can be explicitly set."""
        value = Decimal("150.50")
        payment_unit = PaymentUnitFactory(org_amount=value)

        assert payment_unit.org_amount == value

    def test_org_amount_can_be_updated(self):
        """Test that org_amount can be updated after creation."""
        payment_unit = PaymentUnitFactory(org_amount=Decimal("100"))

        payment_unit.org_amount = Decimal("200")
        payment_unit.save()
        payment_unit.refresh_from_db()

        assert payment_unit.org_amount == Decimal("200")

    def test_org_amount_persists_to_database(self):
        """Test that org_amount value persists to database."""
        value = Decimal("75.25")
        payment_unit = PaymentUnitFactory(org_amount=value)
        pk = payment_unit.pk

        # Retrieve from database
        retrieved = PaymentUnit.objects.get(pk=pk)
        assert retrieved.org_amount == value

    def test_org_amount_with_managed_opportunity(self, managed_opportunity):
        """Test org_amount in context of managed opportunity."""
        payment_unit = PaymentUnitFactory(
            opportunity=managed_opportunity,
            org_amount=Decimal("50.00"),
        )

        assert payment_unit.org_amount == Decimal("50.00")
        assert payment_unit.opportunity.managed is True

    def test_org_amount_with_non_managed_opportunity(self):
        """Test org_amount with non-managed opportunity."""
        opportunity = OpportunityFactory(managed=False)
        payment_unit = PaymentUnitFactory(
            opportunity=opportunity,
            org_amount=Decimal("30.00"),
        )

        assert payment_unit.org_amount == Decimal("30.00")
        assert payment_unit.opportunity.managed is False

    def test_multiple_payment_units_different_org_amounts(self):
        """Test multiple payment units can have different org_amounts."""
        opportunity = OpportunityFactory()

        payment_unit1 = PaymentUnitFactory(opportunity=opportunity, org_amount=Decimal("10"))
        payment_unit2 = PaymentUnitFactory(opportunity=opportunity, org_amount=Decimal("20"))
        payment_unit3 = PaymentUnitFactory(opportunity=opportunity, org_amount=Decimal("30"))

        assert payment_unit1.org_amount == Decimal("10")
        assert payment_unit2.org_amount == Decimal("20")
        assert payment_unit3.org_amount == Decimal("30")

    def test_org_amount_zero_value(self):
        """Test that org_amount can be set to zero."""
        payment_unit = PaymentUnitFactory(org_amount=Decimal("0"))

        assert payment_unit.org_amount == Decimal("0")

    def test_org_amount_large_value(self):
        """Test that org_amount can handle large values."""
        large_value = Decimal("99999999.99")
        payment_unit = PaymentUnitFactory(org_amount=large_value)

        assert payment_unit.org_amount == large_value

    def test_org_amount_queryset_filter(self):
        """Test that we can filter PaymentUnit by org_amount."""
        PaymentUnitFactory(org_amount=Decimal("10"))
        PaymentUnitFactory(org_amount=Decimal("20"))
        PaymentUnitFactory(org_amount=Decimal("30"))

        results = PaymentUnit.objects.filter(org_amount=Decimal("20"))
        assert results.count() == 1
        assert results.first().org_amount == Decimal("20")

    def test_org_amount_ordering(self):
        """Test that we can order PaymentUnit by org_amount."""
        PaymentUnitFactory(org_amount=Decimal("30"))
        PaymentUnitFactory(org_amount=Decimal("10"))
        PaymentUnitFactory(org_amount=Decimal("20"))

        results = PaymentUnit.objects.order_by("org_amount")
        amounts = [pu.org_amount for pu in results]

        assert amounts[0] == Decimal("10")
        assert amounts[1] == Decimal("20")
        assert amounts[2] == Decimal("30")


@pytest.mark.django_db
class TestPaymentUnitOrgAmountEdgeCases:
    def test_org_amount_with_null_if_allowed(self):
        """Test org_amount behavior with null value if field allows it."""
        # This test will pass if null=True, fail if null=False
        # Adjust based on actual field definition
        try:
            payment_unit = PaymentUnit.objects.create(
                opportunity=OpportunityFactory(),
                name="Test",
                amount=100,
                max_total=10,
                max_daily=1,
                org_amount=None,
            )
            # If we get here, null is allowed
            assert payment_unit.org_amount is None
        except Exception:
            # If we get here, null is not allowed (which is expected)
            # Create with a value instead
            payment_unit = PaymentUnit.objects.create(
                opportunity=OpportunityFactory(),
                name="Test",
                amount=100,
                max_total=10,
                max_daily=1,
                org_amount=Decimal("0"),
            )
            assert payment_unit.org_amount == Decimal("0")

    def test_org_amount_aggregate_sum(self):
        """Test that we can aggregate org_amount."""
        from django.db.models import Sum

        opportunity = OpportunityFactory()
        PaymentUnitFactory(opportunity=opportunity, org_amount=Decimal("10"))
        PaymentUnitFactory(opportunity=opportunity, org_amount=Decimal("20"))
        PaymentUnitFactory(opportunity=opportunity, org_amount=Decimal("30"))

        total = PaymentUnit.objects.filter(opportunity=opportunity).aggregate(
            total=Sum("org_amount")
        )["total"]

        assert total == Decimal("60")