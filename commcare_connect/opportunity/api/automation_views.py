from django.shortcuts import get_object_or_404
from django.utils.translation import gettext_lazy as _
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from commcare_connect.opportunity.api.automation_serializers import (
    OpportunityActivateResponseSerializer,
    PaymentUnitListCreateSerializer,
    PaymentUnitResponseSerializer,
)
from commcare_connect.opportunity.models import Opportunity, PaymentUnit
from commcare_connect.organization.decorators import user_is_org_admin
from commcare_connect.program.models import ManagedOpportunity


class OpportunityMixin:
    """Shared logic for views scoped to an opportunity — resolves opportunity and checks permissions."""

    def get_opportunity(self):
        opportunity = get_object_or_404(Opportunity, opportunity_id=self.kwargs["opportunity_id"])
        allowed_orgs = [opportunity.organization]
        if opportunity.managed:
            managed = ManagedOpportunity.objects.get(pk=opportunity.pk)
            allowed_orgs.append(managed.program.organization)
        if not any(user_is_org_admin(self.request.user, org) for org in allowed_orgs):
            self.permission_denied(self.request)
        return opportunity


class PaymentUnitCreateView(OpportunityMixin, APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, opportunity_id):
        opportunity = self.get_opportunity()
        serializer = PaymentUnitListCreateSerializer(
            data=request.data, context={"request": request, "opportunity": opportunity}
        )
        serializer.is_valid(raise_exception=True)
        payment_units = serializer.save()
        return Response(
            {"payment_units": PaymentUnitResponseSerializer(payment_units, many=True).data},
            status=status.HTTP_201_CREATED,
        )


class OpportunityActivateView(OpportunityMixin, APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, opportunity_id):
        opportunity = self.get_opportunity()

        if opportunity.active:
            return Response(
                {"non_field_errors": [_("Opportunity is already active.")]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if opportunity.has_ended:
            return Response(
                {"non_field_errors": [_("Opportunity has ended and cannot be activated.")]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not PaymentUnit.objects.filter(opportunity=opportunity).exists():
            return Response(
                {"non_field_errors": [_("At least one payment unit must exist before activating.")]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        opportunity.active = True
        opportunity.modified_by = request.user.email
        opportunity.save(update_fields=["active", "modified_by", "date_modified"])
        return Response(OpportunityActivateResponseSerializer(opportunity).data)
