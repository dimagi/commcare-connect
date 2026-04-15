from django.urls import path

from commcare_connect.data_export import views

app_name = "data_export"
urlpatterns = [
    path("opp_org_program_list/", views.ProgramOpportunityOrganizationDataView.as_view(), name="opp_org_program_list"),
    path("opportunity/<int:opp_id>/", views.SingleOpportunityDataView.as_view(), name="opportunity_data"),
    path("opportunity/<int:opp_id>/user_data/", views.OpportunityUserDataView.as_view(), name="opportunity_user_data"),
    path("opportunity/<int:opp_id>/user_visits/", views.UserVisitDataView.as_view(), name="user_visit_data"),
    path(
        "opportunity/<int:opp_id>/completed_works/", views.CompletedWorkDataView.as_view(), name="completed_work_data"
    ),
    path("opportunity/<int:opp_id>/payment/", views.PaymentDataView.as_view(), name="payment_data"),
    path("opportunity/<int:opp_id>/invoice/", views.InvoiceDataView.as_view(), name="invoice_data"),
    path("opportunity/<int:opp_id>/assessment/", views.AssessmentDataView.as_view(), name="assessment_data"),
    path(
        "opportunity/<int:opp_id>/completed_module/",
        views.CompletedModuleDataView.as_view(),
        name="completed_module_data",
    ),
    path(
        "opportunity/<int:opp_id>/image/",
        views.ImageView.as_view(),
        name="image_export",
    ),
    path(
        "opportunity/<int:opp_id>/app_structure/",
        views.AppStructureView.as_view(),
        name="app_structure",
    ),
    path("labs_record/", views.LabsRecordDataView.as_view(), name="labs_record_data"),
    path(
        "program/<int:program_id>/opportunity/",
        views.ProgramOpportunityDataView.as_view(),
        name="program_opportunity_data",
    ),
    path(
        "organization/<slug:org_slug>/program/",
        views.OrganizationProgramDataView.as_view(),
        name="organization_program_data",
    ),
    path("opportunity/<int:opp_id>/task_types/", views.TaskTypeDataView.as_view(), name="task_type_data"),
    path("opportunity/<int:opp_id>/assigned_tasks/", views.AssignedTaskDataView.as_view(), name="assigned_task_data"),
    path(
        "opportunity/<int:opp_id>/work_area_groups/",
        views.WorkAreaGroupDataView.as_view(),
        name="work_area_group_data",
    ),
    path("opportunity/<int:opp_id>/work_areas/", views.WorkAreaDataView.as_view(), name="work_area_data"),
    path("llo_entity/", views.LLOEntityDataView.as_view(), name="llo_entity_data"),
]
