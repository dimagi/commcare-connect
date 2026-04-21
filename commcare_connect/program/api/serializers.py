from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from commcare_connect.opportunity.models import Country, Currency, DeliveryType
from commcare_connect.organization.models import Organization
from commcare_connect.program.models import Program


class ProgramCreateSerializer(serializers.ModelSerializer):
    organization = serializers.SlugRelatedField(slug_field="slug", queryset=Organization.objects.all())
    delivery_type = serializers.SlugRelatedField(slug_field="slug", queryset=DeliveryType.objects.all())
    currency = serializers.SlugRelatedField(slug_field="code", queryset=Currency.objects.all())
    country = serializers.SlugRelatedField(slug_field="name", queryset=Country.objects.all())

    class Meta:
        model = Program
        fields = [
            "name",
            "description",
            "organization",
            "delivery_type",
            "budget",
            "currency",
            "country",
            "start_date",
            "end_date",
        ]

    def validate_organization(self, value):
        if not value.program_manager:
            raise serializers.ValidationError(_("Organization must be a program manager organization."))
        return value

    def validate(self, data):
        if data["end_date"] <= data["start_date"]:
            raise serializers.ValidationError({"end_date": _("End date must be after start date.")})
        return data

    def create(self, validated_data):
        user = self.context["request"].user
        return Program.objects.create(
            created_by=user.email,
            modified_by=user.email,
            **validated_data,
        )


class ProgramResponseSerializer(serializers.ModelSerializer):
    organization = serializers.SlugRelatedField(slug_field="slug", read_only=True)
    delivery_type = serializers.SlugRelatedField(slug_field="slug", read_only=True)
    currency = serializers.SlugRelatedField(slug_field="code", read_only=True)
    country = serializers.SlugRelatedField(slug_field="name", read_only=True)

    class Meta:
        model = Program
        fields = [
            "program_id",
            "name",
            "slug",
            "description",
            "organization",
            "delivery_type",
            "budget",
            "currency",
            "country",
            "start_date",
            "end_date",
        ]
