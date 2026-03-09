import datetime
import logging

import waffle
from django.db import transaction
from django.db.models import Q
from django.http import Http404
from django.utils.timezone import now
from rest_framework import viewsets
from rest_framework.generics import RetrieveAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from commcare_connect.flags.switch_names import API_UUID
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
from commcare_connect.users.models import User
from commcare_connect.utils.db import get_object_or_list_by_uuid_or_int
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
        opportunity_access = get_object_or_list_by_uuid_or_int(
            queryset=OpportunityAccess.objects.filter(user=self.request.user),
            pk_or_pk_list=self.kwargs.get("pk"),
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
        return get_object_or_list_by_uuid_or_int(
            queryset=OpportunityAccess.objects.filter(user=self.request.user),
            pk_or_pk_list=self.kwargs.get("pk"),
            uuid_field="opportunity__opportunity_id",
            int_field="opportunity_id",
        )


class ClaimOpportunityView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, *args, **kwargs):
        opportunity_access = get_object_or_list_by_uuid_or_int(
            queryset=OpportunityAccess.objects.filter(user=self.request.user),
            pk_or_pk_list=kwargs.get("pk"),
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


def confirm_payments(request, user: User, payments_data: list):
    if not payments_data or not isinstance(payments_data, list):
        return Response(status=400)

    payment_map = {}

    for item in payments_data:
        payment_id = item.get("id")
        confirmed = item.get("confirmed")

        if payment_id is None:
            return Response(status=400)

        if confirmed and confirmed == "true":
            confirmed = True
        elif confirmed and confirmed == "false":
            confirmed = False
        else:
            return Response({"error_code": ErrorCodes.INVALID_FLAG}, status=400)

        payment_map[str(payment_id)] = confirmed

    payments = get_object_or_list_by_uuid_or_int(
        queryset=Payment.objects.filter(
            Q(organization__memberships__user=user) | Q(opportunity_access__user=user),
        ),
        pk_or_pk_list=list(payment_map.keys()),
        uuid_field="payment_id",
    )

    if len(payments) != len(payment_map):
        raise Http404

    lookup_keys = list(payment_map.keys())
    use_int_pk = not waffle.switch_is_active(API_UUID) and all(val.isdigit() for val in lookup_keys)

    for payment in payments:
        payment_key = str(payment.pk) if use_int_pk else str(payment.payment_id)
        payment.confirmed = payment_map[payment_key]
        payment.confirmation_date = now()

    Payment.objects.bulk_update(payments, ["confirmed", "confirmation_date"])

    return Response(status=200)


class ConfirmPaymentView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, *args, **kwargs):
        payment_data = [{"id": kwargs.get("pk"), "confirmed": self.request.data.get("confirmed")}]
        return confirm_payments(self.request, self.request.user, payment_data)


class ConfirmPaymentsView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        return confirm_payments(request, request.user, request.data.get("payments", []))
