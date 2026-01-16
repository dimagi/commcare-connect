import datetime
import logging

from django.db import transaction
from django.db.models import Q
from django.utils.timezone import now
from rest_framework import viewsets
from rest_framework.generics import RetrieveAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from commcare_connect.opportunity.api.serializers import (
    CompletedWorkSerializer,
    DeliveryProgressSerializer,
    OpportunitySerializer,
    UserLearnProgressSerializer,
)
from commcare_connect.opportunity.models import (
    CompletedWork,
    Opportunity,
    OpportunityAccess,
    OpportunityClaim,
    OpportunityClaimLimit,
    Payment,
)
from commcare_connect.users.helpers import create_hq_user_and_link
from commcare_connect.utils.db import get_object_for_api_version
from commcare_connect.utils.error_codes import ErrorCodes

logger = logging.getLogger(__name__)


class OpportunityViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = OpportunitySerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Opportunity.objects.filter(opportunityaccess__user=self.request.user)


class UserLearnProgressView(RetrieveAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = UserLearnProgressSerializer

    def get_object(self):
        opportunity_access = get_object_for_api_version(
            request=self.request,
            queryset=OpportunityAccess.objects.filter(user=self.request.user),
            pk=self.kwargs.get("pk"),
            uuid_field="opportunity__opportunity_id",
            int_field="opportunity_id",
        )
        return dict(
            completed_modules=opportunity_access.unique_completed_modules,
            assessments=opportunity_access.assessment_set.all(),
        )


class UserVisitViewSet(viewsets.GenericViewSet, viewsets.mixins.ListModelMixin):
    serializer_class = CompletedWorkSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return CompletedWork.objects.filter(
            opportunity_access__opportunity=self.kwargs.get("opportunity_id"),
            opportunity_access__user=self.request.user,
        )


class DeliveryProgressView(RetrieveAPIView):
    serializer_class = DeliveryProgressSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        return get_object_for_api_version(
            request=self.request,
            queryset=OpportunityAccess.objects.filter(user=self.request.user),
            pk=self.kwargs.get("pk"),
            uuid_field="opportunity__opportunity_id",
            int_field="opportunity_id",
        )


class ClaimOpportunityView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, *args, **kwargs):
        opportunity_access = get_object_for_api_version(
            request=self.request,
            queryset=OpportunityAccess.objects.filter(user=self.request.user),
            pk=kwargs.get("pk"),
            uuid_field="opportunity__opportunity_id",
            int_field="opportunity_id",
        )
        opportunity = opportunity_access.opportunity

        if OpportunityClaim.objects.filter(opportunity_access=opportunity_access).exists():
            return Response(status=200, data="Opportunity is already claimed")
        if opportunity.remaining_budget < opportunity.minimum_budget_per_visit:
            return Response({"error_code": ErrorCodes.OPPORTUNITY_FULL}, status=400)
        if opportunity.end_date < datetime.date.today():
            return Response({"error_code": ErrorCodes.OPPORTUNITY_ENDED}, status=400)

        with transaction.atomic():
            claim, created = OpportunityClaim.objects.get_or_create(
                opportunity_access=opportunity_access,
                defaults={
                    "end_date": opportunity.end_date,
                },
            )

            if not created:
                return Response(status=200, data="Opportunity is already claimed")

            OpportunityClaimLimit.create_claim_limits(opportunity, claim)

        domain = opportunity.deliver_app.cc_domain
        user_created = create_hq_user_and_link(self.request.user, domain, opportunity)
        if not user_created:
            return Response({"error_code": ErrorCodes.FAILED_USER_CREATE}, status=400)
        return Response(status=201)


class ConfirmPaymentView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, *args, **kwargs):
        payment = get_object_for_api_version(
            request=self.request,
            queryset=Payment.objects.filter(
                Q(organization__memberships__user=self.request.user) | Q(opportunity_access__user=self.request.user),
            ),
            pk=kwargs.get("pk"),
            uuid_field="payment_id",
            int_field="pk",
        )

        confirmed_value = self.request.data["confirmed"]
        if confirmed_value == "false":
            confirmed = False
        elif confirmed_value == "true":
            confirmed = True
        else:
            return Response({"error_code": ErrorCodes.INVALID_FLAG}, status=400)
        payment.confirmed = confirmed
        payment.confirmation_date = now()
        payment.save()
        return Response(status=200)
