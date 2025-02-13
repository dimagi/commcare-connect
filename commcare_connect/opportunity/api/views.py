import datetime

from django.db import transaction
from django.utils.timezone import now
from rest_framework import viewsets
from rest_framework.generics import RetrieveAPIView, get_object_or_404
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
from commcare_connect.users.helpers import create_hq_user
from commcare_connect.users.models import ConnectIDUserLink


class OpportunityViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = OpportunitySerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Opportunity.objects.filter(opportunityaccess__user=self.request.user)


class UserLearnProgressView(RetrieveAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = UserLearnProgressSerializer

    def get_object(self):
        opportunity_access = get_object_or_404(
            OpportunityAccess, user=self.request.user, opportunity=self.kwargs.get("pk")
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
        return OpportunityAccess.objects.get(user=self.request.user, opportunity=self.kwargs.get("pk"))


class ClaimOpportunityView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, *args, **kwargs):
        opportunity_access = get_object_or_404(OpportunityAccess, user=self.request.user, opportunity=kwargs.get("pk"))
        opportunity = opportunity_access.opportunity

        if OpportunityClaim.objects.filter(opportunity_access=opportunity_access).exists():
            return Response(status=200, data="Opportunity is already claimed")
        if opportunity.remaining_budget < opportunity.minimum_budget_per_visit:
            return Response(status=400, data="Opportunity cannot be claimed. (Budget Exhausted)")
        if opportunity.end_date < datetime.date.today():
            return Response(status=400, data="Opportunity cannot be claimed. (End date reached)")

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
        if not ConnectIDUserLink.objects.filter(user=self.request.user, domain=domain).exists():
            user_created = create_hq_user(self.request.user, domain, opportunity.api_key)
            if not user_created:
                return Response("Failed to create user", status=400)
            cc_username = f"{self.request.user.username.lower()}@{domain}.commcarehq.org"
            ConnectIDUserLink.objects.create(commcare_username=cc_username, user=self.request.user, domain=domain)
        return Response(status=201)


class ConfirmPaymentView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, *args, **kwargs):
        payment = get_object_or_404(Payment, pk=kwargs.get("pk"))
        confirmed_value = self.request.data["confirmed"]
        if confirmed_value == "false":
            confirmed = False
        elif confirmed_value == "true":
            confirmed = True
        else:
            return Response("confirmed must be 'true' or 'false'", status=400)
        payment.confirmed = confirmed
        payment.confirmation_date = now()
        payment.save()
        return Response(status=200)
