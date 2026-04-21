from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from commcare_connect.opportunity.models import DeliverUnit, Opportunity, PaymentUnit


class PaymentUnitItemSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    description = serializers.CharField(required=False, default="")
    amount = serializers.IntegerField(min_value=0)
    org_amount = serializers.IntegerField(min_value=0, required=False)
    max_total = serializers.IntegerField(min_value=1)
    max_daily = serializers.IntegerField(min_value=1)
    required_deliver_units = serializers.ListField(child=serializers.IntegerField(), required=False, default=list)
    optional_deliver_units = serializers.ListField(child=serializers.IntegerField(), required=False, default=list)
    start_date = serializers.DateField(required=False, allow_null=True, default=None)
    end_date = serializers.DateField(required=False, allow_null=True, default=None)

    def validate(self, data):
        opportunity = self.context["opportunity"]

        if opportunity.managed and not data.get("org_amount"):
            raise serializers.ValidationError({"org_amount": _("org_amount is required for managed opportunities.")})

        if data["start_date"] and data["end_date"] and data["end_date"] < data["start_date"]:
            raise serializers.ValidationError({"end_date": _("End date cannot be earlier than start date.")})

        required = data.get("required_deliver_units", [])
        optional = data.get("optional_deliver_units", [])

        overlap = set(required) & set(optional)
        if overlap:
            raise serializers.ValidationError(
                {
                    "required_deliver_units": _("Deliver units cannot be both required and optional: {ids}").format(
                        ids=sorted(overlap)
                    )
                }
            )

        deliver_app = opportunity.deliver_app
        all_du_ids = set(required + optional)
        if all_du_ids:
            valid_ids = set(
                DeliverUnit.objects.filter(app=deliver_app, id__in=all_du_ids, payment_unit__isnull=True).values_list(
                    "id", flat=True
                )
            )
            invalid = all_du_ids - valid_ids
            if invalid:
                raise serializers.ValidationError(
                    {
                        "required_deliver_units": _("Invalid or already-assigned deliver unit IDs: {ids}").format(
                            ids=sorted(invalid)
                        )
                    }
                )

        return data


class PaymentUnitListCreateSerializer(serializers.Serializer):
    payment_units = PaymentUnitItemSerializer(many=True, min_length=1)

    def validate(self, data):
        seen_du_ids = set()
        for item in data["payment_units"]:
            item_dus = set(item.get("required_deliver_units", []) + item.get("optional_deliver_units", []))
            overlap = seen_du_ids & item_dus
            if overlap:
                raise serializers.ValidationError(
                    {
                        "payment_units": _("Deliver units assigned to multiple payment units: {ids}").format(
                            ids=sorted(overlap)
                        )
                    }
                )
            seen_du_ids.update(item_dus)
        return data

    def create(self, validated_data):
        opportunity = self.context["opportunity"]
        created = []
        for pu_data in validated_data["payment_units"]:
            pu = PaymentUnit.objects.create(
                opportunity=opportunity,
                name=pu_data["name"],
                description=pu_data.get("description", ""),
                amount=pu_data["amount"],
                org_amount=pu_data.get("org_amount", 0),
                max_total=pu_data["max_total"],
                max_daily=pu_data["max_daily"],
                start_date=pu_data.get("start_date"),
                end_date=pu_data.get("end_date"),
            )
            for du_id in pu_data.get("required_deliver_units", []):
                DeliverUnit.objects.filter(id=du_id).update(payment_unit=pu, optional=False)
            for du_id in pu_data.get("optional_deliver_units", []):
                DeliverUnit.objects.filter(id=du_id).update(payment_unit=pu, optional=True)
            created.append(pu)
        return created


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
