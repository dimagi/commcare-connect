from django.urls import path

from commcare_connect.solicitation.views import applications, management, marketplace, participants

app_name = "solicitation"
urlpatterns = [
    path("", view=management.SolicitationListView.as_view(), name="list"),
    path("new/", view=management.SolicitationDetailsView.as_view(), name="create"),
    path("<uuid:solicitation_id>/details/", view=management.SolicitationDetailsView.as_view(), name="details"),
    path("<uuid:solicitation_id>/criteria/", view=management.SolicitationCriteriaView.as_view(), name="criteria"),
    path("<uuid:solicitation_id>/questions/", view=management.SolicitationQuestionsView.as_view(), name="questions"),
    path("<uuid:solicitation_id>/publish/", view=management.SolicitationPublishView.as_view(), name="publish"),
    path(
        "<uuid:solicitation_id>/applications/",
        view=applications.SolicitationApplicationsView.as_view(),
        name="applications",
    ),
    path("<uuid:solicitation_id>/close/", view=management.SolicitationCloseView.as_view(), name="close"),
    path("<uuid:solicitation_id>/cancel/", view=management.SolicitationCancelView.as_view(), name="cancel"),
    path(
        "<uuid:solicitation_id>/invitations/",
        view=participants.SolicitationInvitationsView.as_view(),
        name="invitations",
    ),
    path(
        "<uuid:solicitation_id>/invitations/delete/",
        view=participants.SolicitationInvitationDeleteView.as_view(),
        name="delete_invitations",
    ),
    path("<uuid:solicitation_id>/reviewers/", view=participants.SolicitationReviewersView.as_view(), name="reviewers"),
    path(
        "<uuid:solicitation_id>/reviewers/delete/",
        view=participants.SolicitationReviewerDeleteView.as_view(),
        name="delete_reviewers",
    ),
]

# The public marketplace: not scoped to an organization, and reachable without logging in.
marketplace_urlpatterns = [
    path("", view=marketplace.MarketplaceListView.as_view(), name="list"),
]

# The applications a posting organization's solicitations received.
received_application_urlpatterns = [
    path("", view=applications.ReceivedApplicationsView.as_view(), name="list"),
]
