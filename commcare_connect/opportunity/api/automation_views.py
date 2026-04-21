from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from commcare_connect.opportunity.api.automation_serializers import (
    PaymentUnitListCreateSerializer,
    PaymentUnitResponseSerializer,
)
from commcare_connect.opportunity.models import Opportunity
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
