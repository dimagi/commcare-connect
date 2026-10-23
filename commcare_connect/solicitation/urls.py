from django.urls import path

from commcare_connect.solicitation.views import SolicitationDetailsView, SolicitationListView

app_name = "solicitation"
urlpatterns = [
    path("", view=SolicitationListView.as_view(), name="list"),
    path("new/", view=SolicitationDetailsView.as_view(), name="create"),
    path("<uuid:solicitation_id>/details/", view=SolicitationDetailsView.as_view(), name="details"),
]
