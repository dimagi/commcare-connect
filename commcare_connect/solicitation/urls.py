from django.urls import path

from commcare_connect.solicitation.views import SolicitationListView

app_name = "solicitation"
urlpatterns = [
    path("", view=SolicitationListView.as_view(), name="list"),
]
