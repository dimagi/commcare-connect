from rest_framework import serializers, viewsets
from rest_framework.permissions import IsAuthenticated

from commcare_connect.opportunity.models import Country, Currency, DeliveryType


class DeliveryTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = DeliveryType
        fields = ["id", "name", "slug", "description"]


class CurrencySerializer(serializers.ModelSerializer):
    class Meta:
        model = Currency
        fields = ["code", "name"]


class CountrySerializer(serializers.ModelSerializer):
    class Meta:
        model = Country
        fields = ["code", "name", "currency"]


class DeliveryTypeViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = DeliveryType.objects.all()
    serializer_class = DeliveryTypeSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None


class CurrencyViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Currency.objects.order_by("code")
    serializer_class = CurrencySerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None


class CountryViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Country.objects.order_by("name")
    serializer_class = CountrySerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None
