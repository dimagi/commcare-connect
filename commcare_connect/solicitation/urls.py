from django.urls import path

from commcare_connect.solicitation.views import (
    MarketplaceAttachmentView,
    MarketplaceDetailView,
    MarketplaceListView,
    ReceivedApplicationsView,
    SolicitationApplyView,
    SolicitationCancelView,
    SolicitationCloseView,
    SolicitationCriteriaView,
    SolicitationDetailsView,
    SolicitationInvitationDeleteView,
    SolicitationInvitationsView,
    SolicitationListView,
    SolicitationPublishView,
    SolicitationQuestionsView,
    SolicitationReviewerDeleteView,
    SolicitationReviewersView,
)

app_name = "solicitation"
urlpatterns = [
    path("", view=SolicitationListView.as_view(), name="list"),
    path("new/", view=SolicitationDetailsView.as_view(), name="create"),
    path("<uuid:solicitation_id>/details/", view=SolicitationDetailsView.as_view(), name="details"),
    path("<uuid:solicitation_id>/criteria/", view=SolicitationCriteriaView.as_view(), name="criteria"),
    path("<uuid:solicitation_id>/questions/", view=SolicitationQuestionsView.as_view(), name="questions"),
    path("<uuid:solicitation_id>/publish/", view=SolicitationPublishView.as_view(), name="publish"),
    path("<uuid:solicitation_id>/close/", view=SolicitationCloseView.as_view(), name="close"),
    path("<uuid:solicitation_id>/cancel/", view=SolicitationCancelView.as_view(), name="cancel"),
    path("<uuid:solicitation_id>/invitations/", view=SolicitationInvitationsView.as_view(), name="invitations"),
    path(
        "<uuid:solicitation_id>/invitations/delete/",
        view=SolicitationInvitationDeleteView.as_view(),
        name="delete_invitations",
    ),
    path("<uuid:solicitation_id>/reviewers/", view=SolicitationReviewersView.as_view(), name="reviewers"),
    path(
        "<uuid:solicitation_id>/reviewers/delete/",
        view=SolicitationReviewerDeleteView.as_view(),
        name="delete_reviewers",
    ),
]

# The public marketplace: not scoped to an organization, and reachable without logging in.
marketplace_urlpatterns = [
    path("", view=MarketplaceListView.as_view(), name="list"),
    path("<uuid:solicitation_id>/", view=MarketplaceDetailView.as_view(), name="detail"),
    path("<uuid:solicitation_id>/apply/", view=SolicitationApplyView.as_view(), name="apply"),
    path(
        "<uuid:solicitation_id>/attachments/<int:attachment_id>/",
        view=MarketplaceAttachmentView.as_view(),
        name="attachment",
    ),
]

# The applications a posting organization's solicitations received.
received_application_urlpatterns = [
    path("", view=ReceivedApplicationsView.as_view(), name="list"),
]
