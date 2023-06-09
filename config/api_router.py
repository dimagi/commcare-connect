from django.conf import settings
from rest_framework.routers import DefaultRouter, SimpleRouter

from commcare_connect.opportunity.api.views import OpportunityViewSet
from commcare_connect.users.api.views import UserViewSet

if settings.DEBUG:
    router = DefaultRouter()
else:
    router = SimpleRouter()

router.register("users", UserViewSet)
router.register("v1/opportunity", OpportunityViewSet)

app_name = "api"
urlpatterns = router.urls
