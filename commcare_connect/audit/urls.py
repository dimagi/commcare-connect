from django.urls import path

from commcare_connect.audit import oauth_views, views

app_name = "audit"

urlpatterns = [
    # OAuth endpoints for Connect production
    path("oauth/connect/login/", oauth_views.oauth2_login, name="connect_oauth_login"),
    path("oauth/connect/callback/", oauth_views.oauth2_callback, name="connect_oauth_callback"),
    # Audit management
    path("", views.AuditListView.as_view(), name="session_list"),
    path("sessions/<int:pk>/", views.AuditDetailView.as_view(), name="session_detail"),
    path("sessions/<int:pk>/export/", views.AuditExportView.as_view(), name="session_export"),
    path("sessions/<int:pk>/bulk-assessment/", views.BulkAssessmentView.as_view(), name="bulk_assessment"),
    path("export-all/", views.AuditExportAllView.as_view(), name="export_all"),
    # Audit creation wizard
    path("create/", views.AuditCreationWizardView.as_view(), name="creation_wizard"),
    # AJAX endpoints
    path("api/results/<int:session_id>/update/", views.AuditResultUpdateView.as_view(), name="result_update"),
    path("api/assessment/<int:assessment_id>/update/", views.AssessmentUpdateView.as_view(), name="assessment_update"),
    path("api/visit/<int:visit_id>/result/", views.VisitResultUpdateView.as_view(), name="visit_result_update"),
    path(
        "api/sessions/<int:session_id>/apply-results/",
        views.ApplyAssessmentResultsView.as_view(),
        name="apply_assessment_results",
    ),
    path("api/sessions/<int:session_id>/complete/", views.AuditCompleteView.as_view(), name="session_complete"),
    path(
        "api/sessions/<int:session_id>/uncomplete/",
        views.AuditUncompleteView.as_view(),
        name="session_uncomplete",
    ),
    path("api/sessions/<int:session_id>/visit-data/", views.AuditVisitDataView.as_view(), name="visit_data"),
    # Audit creation API endpoints
    path("api/programs/search/", views.ProgramSearchAPIView.as_view(), name="program_search"),
    path(
        "api/programs/<int:program_id>/opportunities/",
        views.ProgramOpportunitiesAPIView.as_view(),
        name="program_opportunities",
    ),
    path("api/audit/preview/", views.AuditPreviewAPIView.as_view(), name="preview_audit"),
    path("api/audit/create/", views.AuditCreateAPIView.as_view(), name="create_session"),
    path("api/audit/progress/", views.AuditProgressAPIView.as_view(), name="audit_progress"),
    # Database management
    path("api/database/stats/", views.DatabaseStatsAPIView.as_view(), name="database_stats"),
    path("api/database/reset/", views.DatabaseResetAPIView.as_view(), name="reset_database"),
    path(
        "api/database/download-missing-attachments/",
        views.DownloadMissingAttachmentsAPIView.as_view(),
        name="download_missing_attachments",
    ),
    # Audit template import/export
    path(
        "api/template/<int:definition_id>/export/",
        views.AuditTemplateExportView.as_view(),
        name="definition_export",
    ),
    path("api/template/import/", views.AuditTemplateImportView.as_view(), name="definition_import"),
    # Image serving
    path("image/<str:blob_id>/", views.AuditImageView.as_view(), name="image"),
]
