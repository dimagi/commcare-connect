from django.urls import path

from commcare_connect.solicitation.views import (
    SolicitationCriteriaView,
    SolicitationDetailsView,
    SolicitationListView,
    SolicitationQuestionsView,
)

app_name = "solicitation"
urlpatterns = [
    path("", view=SolicitationListView.as_view(), name="list"),
    path("new/", view=SolicitationDetailsView.as_view(), name="create"),
    path("<uuid:solicitation_id>/details/", view=SolicitationDetailsView.as_view(), name="details"),
    path("<uuid:solicitation_id>/criteria/", view=SolicitationCriteriaView.as_view(), name="criteria"),
    path("<uuid:solicitation_id>/questions/", view=SolicitationQuestionsView.as_view(), name="questions"),
]
