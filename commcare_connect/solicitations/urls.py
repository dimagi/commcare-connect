from django.urls import path

from .views import (
    SolicitationCreateOrUpdate,
    SolicitationDetailView,
    SolicitationListView,
    SolicitationResponseCreateOrUpdate,
    SolicitationResponseDetailView,
    SolicitationResponseDraftListView,
    SolicitationResponseReviewCreateOrUpdate,
    SolicitationResponseSuccessView,
    SolicitationResponseTableView,
    UnifiedSolicitationDashboard,
    delete_attachment,
    save_draft_ajax,
    upload_attachment,
)

app_name = "solicitations"

urlpatterns = [
    # Public URLs (no authentication required)
    path("", SolicitationListView.as_view(), name="list"),
    path("eoi/", SolicitationListView.as_view(), {"type": "eoi"}, name="eoi_list"),
    path("rfp/", SolicitationListView.as_view(), {"type": "rfp"}, name="rfp_list"),
    path("s/<int:pk>/", SolicitationDetailView.as_view(), name="detail"),
    # dashboards
    path("admin-overview/", UnifiedSolicitationDashboard.as_view(), name="admin_overview"),
    path("dashboard/", UnifiedSolicitationDashboard.as_view(), name="dashboard"),
    # Authenticated response submission
    path("s/<int:solicitation_pk>/respond/", SolicitationResponseCreateOrUpdate.as_view(), name="respond"),
    path("response/<int:pk>/edit/", SolicitationResponseCreateOrUpdate.as_view(), name="user_response_edit"),
    path("response/<int:pk>/success/", SolicitationResponseSuccessView.as_view(), name="response_success"),
    # User response management
    path("response/<int:pk>/", SolicitationResponseDetailView.as_view(), name="user_response_detail"),
    # Draft management
    path("drafts/", SolicitationResponseDraftListView.as_view(), name="draft_list"),
    path("s/<int:pk>/save-draft/", save_draft_ajax, name="save_draft"),
    # File management
    path("s/<int:pk>/upload-attachment/", upload_attachment, name="upload_attachment"),
    path("s/<int:pk>/delete-attachment/<int:attachment_id>/", delete_attachment, name="delete_attachment"),
    # Program management URLs (moved from program app)
    path("program/<int:pk>/", UnifiedSolicitationDashboard.as_view(), name="program_dashboard"),
    path(
        "program/<int:pk>/solicitations/<int:solicitation_pk>/responses/",
        SolicitationResponseTableView.as_view(),
        name="program_response_list",
    ),
    # CONSOLIDATED REVIEW VIEW (PRIMARY)
    path(
        "program/<int:pk>/solicitations/response/<int:response_pk>/review/",
        SolicitationResponseReviewCreateOrUpdate.as_view(),
        name="program_response_review",
    ),
    # CONSOLIDATED VIEWS (PRIMARY)
    path(
        "program/<int:program_pk>/solicitations/create/",
        SolicitationCreateOrUpdate.as_view(),
        name="program_solicitation_create",
    ),
    path(
        "program/<int:program_pk>/solicitations/<int:pk>/edit/",
        SolicitationCreateOrUpdate.as_view(),
        name="program_solicitation_edit",
    ),
]
