"""Edge case and integration tests for data_export serializers."""
import pytest
from collections import OrderedDict
from decimal import Decimal

from commcare_connect.data_export.serializer import (
    OpportunityDataExportSerializer,
    OpportunitySerializer,
    OpportunityUserDataSerializer,
)
from commcare_connect.opportunity.tests.factories import (
    OpportunityAccessFactory,
    OpportunityClaimFactory,
    OpportunityClaimLimitFactory,
    OpportunityFactory,
    PaymentUnitFactory,
)
from commcare_connect.users.tests.factories import MobileUserFactory


@pytest.mark.django_db
class TestOpportunityDataExportSerializerEdgeCases:
    def test_serialization_with_no_visit_count_annotation(self):
        """Test serialization when visit_count annotation is missing."""
        opportunity = OpportunityFactory()
        # Don't add visit_count annotation
        serializer = OpportunityDataExportSerializer(opportunity)
        data = serializer.data

        # Should default to 0 when annotation is missing
        assert data["visit_count"] == 0

    def test_get_org_pay_per_visit_non_managed(self):
        """Test org_pay_per_visit for non-managed opportunity."""
        opportunity = OpportunityFactory(managed=False)
        serializer = OpportunityDataExportSerializer(opportunity)
        data = serializer.data

        # Should return 0 for non-managed opportunities
        assert data["org_pay_per_visit"] == 0

    def test_serialization_with_null_end_date(self):
        """Test serialization when end_date is null."""
        opportunity = OpportunityFactory(end_date=None)
        serializer = OpportunityDataExportSerializer(opportunity)
        data = serializer.data

        assert data["end_date"] is None


@pytest.mark.django_db
class TestOpportunityUserDataSerializerEdgeCases:
    def test_get_claim_limits_empty(self):
        """Test get_claim_limits when no claim limits exist."""
        access = OpportunityAccessFactory()
        access.username = access.user.username
        serializer = OpportunityUserDataSerializer(access)
        data = serializer.data

        assert data["claim_limits"] == []

    def test_get_claim_limits_multiple(self):
        """Test get_claim_limits with multiple payment units."""
        access = OpportunityAccessFactory()
        claim = OpportunityClaimFactory(opportunity_access=access)

        payment_unit1 = PaymentUnitFactory(opportunity=access.opportunity)
        payment_unit2 = PaymentUnitFactory(opportunity=access.opportunity)

        OpportunityClaimLimitFactory(
            opportunity_claim=claim, payment_unit=payment_unit1, max_visits=10
        )
        OpportunityClaimLimitFactory(
            opportunity_claim=claim, payment_unit=payment_unit2, max_visits=20
        )

        access.username = access.user.username
        serializer = OpportunityUserDataSerializer(access)
        data = serializer.data

        assert len(data["claim_limits"]) == 2
        max_visits = {cl["max_visits"] for cl in data["claim_limits"]}
        assert max_visits == {10, 20}

    def test_serialization_with_none_values(self):
        """Test serialization with None values for nullable fields."""
        access = OpportunityAccessFactory()
        access.username = access.user.username
        access.name = None
        access.phone = None
        access.payment_accrued = None
        access.suspension_date = None
        access.suspension_reason = None
        access.invited_date = None
        access.completed_learn_date = None
        access.last_active = None
        access.date_claimed = None

        serializer = OpportunityUserDataSerializer(access)
        data = serializer.data

        # Should handle None values gracefully
        assert data is not None


@pytest.mark.django_db
class TestOpportunitySerializerOrderedDictConversion:
    def test_to_representation_converts_nested_ordered_dicts(self):
        """Test that nested OrderedDicts are converted to regular dicts."""
        opportunity = OpportunityFactory()
        user = MobileUserFactory()
        OpportunityAccessFactory(opportunity=opportunity, user=user)
        PaymentUnitFactory(opportunity=opportunity)

        request = MockRequest()
        request.user = user
        serializer = OpportunitySerializer(opportunity, context={"request": request})
        data = serializer.data

        # Verify no OrderedDict instances in the result
        assert not isinstance(data, OrderedDict)
        assert isinstance(data, dict)

        if "learn_app" in data:
            assert isinstance(data["learn_app"], dict)
            assert not isinstance(data["learn_app"], OrderedDict)

        if "deliver_app" in data:
            assert isinstance(data["deliver_app"], dict)
            assert not isinstance(data["deliver_app"], OrderedDict)

        if "payment_units" in data and data["payment_units"]:
            for pu in data["payment_units"]:
                assert isinstance(pu, dict)
                assert not isinstance(pu, OrderedDict)

    def test_to_representation_empty_lists(self):
        """Test that empty lists are handled correctly."""
        opportunity = OpportunityFactory()
        user = MobileUserFactory()
        OpportunityAccessFactory(opportunity=opportunity, user=user)
        # Don't create any payment units

        request = MockRequest()
        request.user = user
        serializer = OpportunitySerializer(opportunity, context={"request": request})
        data = serializer.data

        # Should handle empty payment_units list
        assert isinstance(data["payment_units"], list)

    def test_to_representation_deeply_nested_ordered_dicts(self):
        """Test conversion of deeply nested OrderedDict structures."""
        opportunity = OpportunityFactory()
        user = MobileUserFactory()
        OpportunityAccessFactory(opportunity=opportunity, user=user)

        # Create multiple payment units to test list handling
        PaymentUnitFactory(opportunity=opportunity)
        PaymentUnitFactory(opportunity=opportunity)

        request = MockRequest()
        request.user = user
        serializer = OpportunitySerializer(opportunity, context={"request": request})
        data = serializer.data

        # Recursively check no OrderedDict remains
        def check_no_ordered_dict(obj):
            if isinstance(obj, OrderedDict):
                return False
            if isinstance(obj, dict):
                return all(check_no_ordered_dict(v) for v in obj.values())
            if isinstance(obj, list):
                return all(check_no_ordered_dict(item) for item in obj)
            return True

        assert check_no_ordered_dict(data), "Found OrderedDict in serialized data"


class MockRequest:
    """Mock request object for testing."""

    def __init__(self):
        self.user = None


@pytest.mark.django_db
class TestSerializerPerformance:
    def test_serialization_with_many_payment_units(self):
        """Test serialization performance with many payment units."""
        opportunity = OpportunityFactory()
        user = MobileUserFactory()
        OpportunityAccessFactory(opportunity=opportunity, user=user)

        # Create many payment units
        for i in range(20):
            PaymentUnitFactory(opportunity=opportunity, name=f"Unit {i}")

        request = MockRequest()
        request.user = user
        serializer = OpportunitySerializer(opportunity, context={"request": request})
        data = serializer.data

        assert len(data["payment_units"]) == 20

    def test_serialization_with_many_claim_limits(self):
        """Test serialization with many claim limits."""
        access = OpportunityAccessFactory()
        claim = OpportunityClaimFactory(opportunity_access=access)

        # Create many claim limits
        for i in range(15):
            payment_unit = PaymentUnitFactory(opportunity=access.opportunity)
            OpportunityClaimLimitFactory(
                opportunity_claim=claim, payment_unit=payment_unit, max_visits=i + 1
            )

        access.username = access.user.username
        serializer = OpportunityUserDataSerializer(access)
        data = serializer.data

        assert len(data["claim_limits"]) == 15


@pytest.mark.django_db
class TestSerializerDataIntegrity:
    def test_serializer_preserves_decimal_precision(self):
        """Test that decimal values maintain precision through serialization."""
        opportunity = OpportunityFactory()
        payment_unit = PaymentUnitFactory(
            opportunity=opportunity,
            amount=Decimal("123.456789"),
        )

        from commcare_connect.opportunity.api.serializers import PaymentUnitSerializer

        serializer = PaymentUnitSerializer(payment_unit)
        data = serializer.data

        # Decimal should be preserved (as string in JSON)
        assert data["amount"] == payment_unit.amount

    def test_serializer_handles_special_characters_in_names(self):
        """Test that special characters in names are handled correctly."""
        opportunity = OpportunityFactory(name="Test & <Opportunity> 'with' \"quotes\"")
        serializer = OpportunityDataExportSerializer(opportunity)
        data = serializer.data

        assert data["name"] == "Test & <Opportunity> 'with' \"quotes\""