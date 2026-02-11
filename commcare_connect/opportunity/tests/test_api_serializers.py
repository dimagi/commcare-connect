"""Tests for opportunity.api.serializers module."""
import pytest
from decimal import Decimal
from unittest.mock import Mock, patch

from commcare_connect.opportunity.api.serializers import (
    PaymentUnitSerializer,
    OpportunitySerializer,
    _get_opp_access,
    remove_opportunity_access_cache,
)
from commcare_connect.opportunity.models import OpportunityAccess
from commcare_connect.opportunity.tests.factories import (
    OpportunityAccessFactory,
    OpportunityFactory,
    PaymentUnitFactory,
)
from commcare_connect.users.tests.factories import MobileUserFactory


@pytest.mark.django_db
class TestPaymentUnitSerializer:
    def test_serialization_basic_fields(self):
        """Test that PaymentUnitSerializer includes all basic fields."""
        payment_unit = PaymentUnitFactory()
        serializer = PaymentUnitSerializer(payment_unit)
        data = serializer.data

        assert data["id"] == payment_unit.id
        assert data["name"] == payment_unit.name
        assert data["max_total"] == payment_unit.max_total
        assert data["max_daily"] == payment_unit.max_daily
        assert data["amount"] == payment_unit.amount

    def test_serialization_includes_end_date(self):
        """Test that PaymentUnitSerializer includes end_date field."""
        payment_unit = PaymentUnitFactory()
        serializer = PaymentUnitSerializer(payment_unit)
        data = serializer.data

        assert "end_date" in data
        # end_date can be None or a date
        assert data["end_date"] is None or isinstance(data["end_date"], str)

    def test_serialization_with_end_date_set(self):
        """Test serialization when end_date is explicitly set."""
        from datetime import date
        end_date = date(2025, 12, 31)
        payment_unit = PaymentUnitFactory(end_date=end_date)
        serializer = PaymentUnitSerializer(payment_unit)
        data = serializer.data

        assert data["end_date"] == str(end_date)

    def test_multiple_payment_units(self):
        """Test serialization of multiple payment units."""
        payment_units = [PaymentUnitFactory() for _ in range(3)]
        serializer = PaymentUnitSerializer(payment_units, many=True)
        data = serializer.data

        assert len(data) == 3
        for i, item in enumerate(data):
            assert item["id"] == payment_units[i].id
            assert "end_date" in item


@pytest.mark.django_db
class TestOpportunitySerializer:
    def test_currency_serialization_with_currency_fk_id(self):
        """Test that currency field uses currency_fk_id."""
        opportunity = OpportunityFactory()
        user = MobileUserFactory()
        OpportunityAccessFactory(opportunity=opportunity, user=user)

        request = Mock()
        request.user = user
        serializer = OpportunitySerializer(opportunity, context={"request": request})
        data = serializer.data

        assert data["currency"] == opportunity.currency_fk_id

    def test_currency_field_source(self):
        """Test that currency field correctly maps to currency_fk_id."""
        from rest_framework import serializers

        # Check field definition
        serializer = OpportunitySerializer()
        currency_field = serializer.fields.get("currency")

        assert currency_field is not None
        assert isinstance(currency_field, serializers.CharField)
        assert currency_field.source == "currency_fk_id"
        assert currency_field.read_only is True


@pytest.mark.django_db
class TestCachingFunctions:
    def test_get_opp_access_caches_result(self):
        """Test that _get_opp_access caches the opportunity access."""
        user = MobileUserFactory()
        opportunity = OpportunityFactory()
        access = OpportunityAccessFactory(user=user, opportunity=opportunity)

        # First call should query database
        result1 = _get_opp_access(user, opportunity)
        assert result1 == access

        # Second call should use cache (same result)
        result2 = _get_opp_access(user, opportunity)
        assert result2 == access
        assert result1.pk == result2.pk

    def test_get_opp_access_returns_none_when_not_exists(self):
        """Test that _get_opp_access returns None when access doesn't exist."""
        user = MobileUserFactory()
        opportunity = OpportunityFactory()

        result = _get_opp_access(user, opportunity)
        assert result is None

    def test_remove_opportunity_access_cache(self):
        """Test that remove_opportunity_access_cache clears the cache."""
        user = MobileUserFactory()
        opportunity = OpportunityFactory()
        access = OpportunityAccessFactory(user=user, opportunity=opportunity)

        # Cache the access
        _get_opp_access(user, opportunity)

        # Clear the cache
        remove_opportunity_access_cache(user, opportunity)

        # Next call should query database again
        result = _get_opp_access(user, opportunity)
        assert result == access

    def test_get_opp_access_cache_key_varies_by_user_and_opportunity(self):
        """Test that cache varies by user and opportunity."""
        user1 = MobileUserFactory()
        user2 = MobileUserFactory()
        opportunity1 = OpportunityFactory()
        opportunity2 = OpportunityFactory()

        access1 = OpportunityAccessFactory(user=user1, opportunity=opportunity1)
        access2 = OpportunityAccessFactory(user=user2, opportunity=opportunity2)

        result1 = _get_opp_access(user1, opportunity1)
        result2 = _get_opp_access(user2, opportunity2)

        assert result1 == access1
        assert result2 == access2
        assert result1.pk != result2.pk


@pytest.mark.django_db
class TestOpportunitySerializerPaymentUnits:
    def test_payment_units_field(self):
        """Test that payment_units field correctly serializes payment units."""
        opportunity = OpportunityFactory()
        user = MobileUserFactory()
        OpportunityAccessFactory(opportunity=opportunity, user=user)

        # Create multiple payment units
        payment_unit1 = PaymentUnitFactory(opportunity=opportunity, name="Unit 1")
        payment_unit2 = PaymentUnitFactory(opportunity=opportunity, name="Unit 2")

        request = Mock()
        request.user = user
        serializer = OpportunitySerializer(opportunity, context={"request": request})
        data = serializer.data

        assert "payment_units" in data
        assert len(data["payment_units"]) == 2

        # Verify payment units are properly serialized
        payment_unit_ids = {pu["id"] for pu in data["payment_units"]}
        assert payment_unit1.id in payment_unit_ids
        assert payment_unit2.id in payment_unit_ids

        # Verify end_date is included in each payment unit
        for pu_data in data["payment_units"]:
            assert "end_date" in pu_data

    def test_payment_units_ordered_by_pk(self):
        """Test that payment units are ordered by primary key."""
        opportunity = OpportunityFactory()
        user = MobileUserFactory()
        OpportunityAccessFactory(opportunity=opportunity, user=user)

        # Create payment units (they'll have sequential PKs)
        payment_unit1 = PaymentUnitFactory(opportunity=opportunity, name="Z Unit")
        payment_unit2 = PaymentUnitFactory(opportunity=opportunity, name="A Unit")

        request = Mock()
        request.user = user
        serializer = OpportunitySerializer(opportunity, context={"request": request})
        data = serializer.data

        # Should be ordered by PK, not name
        assert data["payment_units"][0]["id"] == payment_unit1.id
        assert data["payment_units"][1]["id"] == payment_unit2.id


@pytest.mark.django_db
class TestSerializerIntegration:
    def test_full_opportunity_serialization_with_payment_units(self):
        """Integration test for full opportunity serialization including payment units."""
        opportunity = OpportunityFactory()
        user = MobileUserFactory()
        OpportunityAccessFactory(opportunity=opportunity, user=user, accepted=True)

        payment_unit = PaymentUnitFactory(
            opportunity=opportunity,
            name="Test Payment Unit",
            amount=100,
            max_total=50,
            max_daily=5,
        )

        request = Mock()
        request.user = user
        serializer = OpportunitySerializer(opportunity, context={"request": request})
        data = serializer.data

        # Verify basic fields
        assert data["id"] == opportunity.id
        assert data["name"] == opportunity.name
        assert data["currency"] == opportunity.currency_fk_id

        # Verify payment units
        assert len(data["payment_units"]) == 1
        pu_data = data["payment_units"][0]
        assert pu_data["name"] == "Test Payment Unit"
        assert pu_data["amount"] == 100
        assert pu_data["max_total"] == 50
        assert pu_data["max_daily"] == 5
        assert "end_date" in pu_data