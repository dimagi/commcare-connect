from django.urls import path

from commcare_connect.solicitation.views import (
    SolicitationCriteriaView,
    SolicitationDetailsView,
    SolicitationInvitationDeleteView,
    SolicitationInvitationsView,
    SolicitationListView,
    SolicitationPublishView,
    SolicitationQuestionsView,
)

app_name = "solicitation"
urlpatterns = [
    path("", view=SolicitationListView.as_view(), name="list"),
    path("new/", view=SolicitationDetailsView.as_view(), name="create"),
    path("<uuid:solicitation_id>/details/", view=SolicitationDetailsView.as_view(), name="details"),
    path("<uuid:solicitation_id>/criteria/", view=SolicitationCriteriaView.as_view(), name="criteria"),
    path("<uuid:solicitation_id>/questions/", view=SolicitationQuestionsView.as_view(), name="questions"),
    path("<uuid:solicitation_id>/publish/", view=SolicitationPublishView.as_view(), name="publish"),
    path("<uuid:solicitation_id>/invitations/", view=SolicitationInvitationsView.as_view(), name="invitations"),
    path(
        "<uuid:solicitation_id>/invitations/delete/",
        view=SolicitationInvitationDeleteView.as_view(),
        name="delete_invitations",
    ),
]
