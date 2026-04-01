import datetime
import logging

import waffle
from django.db import transaction
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils.timezone import now
from oauth2_provider.contrib.rest_framework.permissions import TokenHasScope
from rest_framework import status, viewsets
from rest_framework.generics import RetrieveAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from commcare_connect.flags.switch_names import API_UUID
from commcare_connect.opportunity.api.permissions import IsOrgProgramManagerAdmin
from commcare_connect.opportunity.api.serializers import (
    CompletedWorkSerializer,
    DeliverUnitCreateSerializer,
    DeliverUnitReadSerializer,
    DeliveryProgressSerializer,
    InviteUsersSerializer,
    OpportunitySerializer,
    PaymentUnitCreateSerializer,
    PaymentUnitSerializer,
    UserLearnProgressSerializer,
)
from commcare_connect.opportunity.models import (
    CompletedWork,
    DeliverUnit,
    Opportunity,
    OpportunityAccess,
    OpportunityClaim,
    OpportunityClaimLimit,
    Payment,
    PaymentUnit,
)
from commcare_connect.opportunity.tasks import add_connect_users
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


class ReadSerializerResponseMixin:
    """Return the read serializer in create/update responses instead of the write serializer."""

    read_serializer_class = None

    def create(self, request, *args, **kwargs):
        write_serializer = self.get_serializer(data=request.data)
        write_serializer.is_valid(raise_exception=True)
        self.perform_create(write_serializer)
        read_serializer = self.read_serializer_class(write_serializer.instance, context=self.get_serializer_context())
        headers = self.get_success_headers(read_serializer.data)
        return Response(read_serializer.data, status=status.HTTP_201_CREATED, headers=headers)


class PaymentUnitViewSet(ReadSerializerResponseMixin, viewsets.ModelViewSet):
    serializer_class = PaymentUnitSerializer
    read_serializer_class = PaymentUnitSerializer
    permission_classes = [IsAuthenticated]
    required_scopes = ["create"]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_permissions(self):
        if self.action in ("create", "partial_update", "destroy"):
            return [IsAuthenticated(), TokenHasScope(), IsOrgProgramManagerAdmin()]
        return [IsAuthenticated()]

    def get_serializer_class(self):
        if self.action in ("create", "partial_update"):
            return PaymentUnitCreateSerializer
        return PaymentUnitSerializer

    def get_opportunity(self):
        return get_object_or_404(Opportunity, opportunity_id=self.kwargs["opportunity_id"])

    def get_serializer_context(self):
        context = super().get_serializer_context()
        if self.kwargs.get("opportunity_id"):
            context["opportunity"] = self.get_opportunity()
        return context

    def get_queryset(self):
        user = self.request.user
        return (
            PaymentUnit.objects.filter(
                opportunity__opportunity_id=self.kwargs["opportunity_id"],
            )
            .filter(
                Q(opportunity__organization__memberships__user=user)
                | Q(opportunity__managedopportunity__program__organization__memberships__user=user)
            )
            .distinct()
            .order_by("pk")
        )


class DeliverUnitViewSet(ReadSerializerResponseMixin, viewsets.ModelViewSet):
    serializer_class = DeliverUnitReadSerializer
    read_serializer_class = DeliverUnitReadSerializer
    permission_classes = [IsAuthenticated]
    required_scopes = ["create"]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_permissions(self):
        if self.action in ("create", "partial_update", "destroy"):
            return [IsAuthenticated(), TokenHasScope(), IsOrgProgramManagerAdmin()]
        return [IsAuthenticated()]

    def get_serializer_class(self):
        if self.action in ("create", "partial_update"):
            return DeliverUnitCreateSerializer
        return DeliverUnitReadSerializer

    def get_opportunity(self):
        return get_object_or_404(Opportunity, opportunity_id=self.kwargs["opportunity_id"])

    def get_serializer_context(self):
        context = super().get_serializer_context()
        if self.kwargs.get("opportunity_id"):
            context["opportunity"] = self.get_opportunity()
        return context

    def get_queryset(self):
        """Filter deliver units by those linked to payment units of this opportunity."""
        user = self.request.user
        return (
            DeliverUnit.objects.filter(
                payment_unit__opportunity__opportunity_id=self.kwargs["opportunity_id"],
            )
            .filter(
                Q(payment_unit__opportunity__organization__memberships__user=user)
                | Q(payment_unit__opportunity__managedopportunity__program__organization__memberships__user=user)
            )
            .distinct()
            .order_by("pk")
        )


class InviteUsersView(APIView):
    permission_classes = [IsAuthenticated, TokenHasScope, IsOrgProgramManagerAdmin]
    required_scopes = ["create"]  # POST-only view, always requires create scope

    def post(self, request, opportunity_id):
        opportunity = get_object_or_404(Opportunity, opportunity_id=opportunity_id)
        if opportunity.has_ended:
            return Response(
                {"error": "This opportunity has ended. You cannot invite more workers."},
                status=400,
            )
        serializer = InviteUsersSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        phone_numbers = serializer.validated_data["phone_numbers"]
        add_connect_users.delay(phone_numbers, str(opportunity.pk))
        return Response({"invited": len(phone_numbers)})
