from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from commcare_connect.opportunity.models import Opportunity, PaymentUnit
from commcare_connect.opportunity.services.payment_units import PaymentUnitInput


class PaymentUnitItemSerializer(serializers.Serializer):
    """Parses one payment unit. The rules about it are checked by ``create_payment_units``."""

    name = serializers.CharField(max_length=255)
    description = serializers.CharField(required=False, default="")
    amount = serializers.IntegerField(min_value=0)
    org_amount = serializers.IntegerField(min_value=0)
    max_total = serializers.IntegerField(min_value=1)
    # Required, but null opts the payment unit out of a daily limit when OPTIONAL_DAILY_LIMIT is on
    max_daily = serializers.IntegerField(min_value=1, allow_null=True)
    required_deliver_units = serializers.ListField(child=serializers.IntegerField(), required=False, default=list)
    optional_deliver_units = serializers.ListField(child=serializers.IntegerField(), required=False, default=list)
    start_date = serializers.DateField(required=False, allow_null=True, default=None)
    end_date = serializers.DateField(required=False, allow_null=True, default=None)


class PaymentUnitListCreateSerializer(serializers.Serializer):
    payment_units = PaymentUnitItemSerializer(many=True, min_length=1)

    def to_inputs(self):
        return [PaymentUnitInput(**item) for item in self.validated_data["payment_units"]]


class PaymentUnitResponseSerializer(serializers.ModelSerializer):
    required_deliver_units = serializers.SerializerMethodField()
    optional_deliver_units = serializers.SerializerMethodField()

    class Meta:
        model = PaymentUnit
        fields = [
            "id",
            "payment_unit_id",
            "name",
            "description",
            "amount",
            "org_amount",
            "max_total",
            "max_daily",
            "required_deliver_units",
            "optional_deliver_units",
            "start_date",
            "end_date",
        ]

    def get_required_deliver_units(self, obj):
        return list(obj.deliver_units.filter(optional=False).values_list("id", flat=True))

    def get_optional_deliver_units(self, obj):
        return list(obj.deliver_units.filter(optional=True).values_list("id", flat=True))


class OpportunityActivateResponseSerializer(serializers.ModelSerializer):
    class Meta:
        model = Opportunity
        fields = ["id", "opportunity_id", "name", "active"]


class UserInviteSerializer(serializers.Serializer):
    phone_numbers = serializers.ListField(child=serializers.CharField(), min_length=1)

    def validate(self, data):
        opportunity = self.context["opportunity"]
        if not opportunity.is_setup_complete:
            raise serializers.ValidationError(_("Please finish setting up the opportunity before inviting users."))
        if not opportunity.active:
            raise serializers.ValidationError(_("Opportunity must be active to invite users."))
        if opportunity.has_ended:
            raise serializers.ValidationError(_("Opportunity has ended."))
        return data
