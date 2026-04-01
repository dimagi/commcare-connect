from django.conf import settings
from django.urls import include, path
from rest_framework.routers import DefaultRouter, SimpleRouter

from commcare_connect.form_receiver.views import FormReceiver
from commcare_connect.opportunity.api.lookups import CountryViewSet, CurrencyViewSet, DeliveryTypeViewSet
from commcare_connect.opportunity.api.views import (
    ClaimOpportunityView,
    ConfirmPaymentsView,
    ConfirmPaymentView,
    DeliverUnitViewSet,
    DeliveryProgressView,
    InviteUsersView,
    OpportunityViewSet,
    PaymentUnitViewSet,
    UserLearnProgressView,
    UserVisitViewSet,
)
from commcare_connect.program.api.views import ManagedOpportunityViewSet, ProgramApplicationViewSet, ProgramViewSet
from commcare_connect.users.api.views import UserViewSet

if settings.DEBUG:
    router = DefaultRouter()
else:
    router = SimpleRouter()

router.register("users", UserViewSet)
router.register("opportunity", OpportunityViewSet, basename="Opportunity")
router.register("opportunity/(?P<opportunity_id>.+)/user_visit", UserVisitViewSet, basename="UserVisit")
router.register(
    "opportunity/(?P<opportunity_id>[^/.]+)/payment_units",
    PaymentUnitViewSet,
    basename="PaymentUnit",
)
router.register(
    "opportunity/(?P<opportunity_id>[^/.]+)/deliver_units",
    DeliverUnitViewSet,
    basename="DeliverUnit",
)
router.register("lookups/delivery_types", DeliveryTypeViewSet, basename="DeliveryType")
router.register("lookups/currencies", CurrencyViewSet, basename="Currency")
router.register("lookups/countries", CountryViewSet, basename="Country")
router.register("program", ProgramViewSet, basename="Program")
router.register(
    "program/(?P<program_id>[^/.]+)/opportunity",
    ManagedOpportunityViewSet,
    basename="ManagedOpportunity",
)
router.register(
    "program/(?P<program_id>[^/.]+)/applications",
    ProgramApplicationViewSet,
    basename="ProgramApplication",
)

app_name = "api"
urlpatterns = [
    path("", include(router.urls)),
    path("receiver/", FormReceiver.as_view(), name="receiver"),
    path("opportunity/<slug:pk>/learn_progress", UserLearnProgressView.as_view(), name="learn_progress"),
    path("opportunity/<slug:pk>/claim", ClaimOpportunityView.as_view()),
    path("opportunity/<slug:pk>/delivery_progress", DeliveryProgressView.as_view(), name="deliver_progress"),
    path("payment/<slug:pk>/confirm", ConfirmPaymentView.as_view(), name="confirm_payment"),
    path("payment/confirm", ConfirmPaymentsView.as_view(), name="confirm_payments"),
    path("opportunity/<slug:opportunity_id>/invite/", InviteUsersView.as_view(), name="invite_users"),
]
