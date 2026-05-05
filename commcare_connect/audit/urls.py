from django.urls import path

from commcare_connect.audit import views

app_name = "audit"

urlpatterns = [
    path(
        "<uuid:opportunity_id>/audit_reports/",
        views.audit_report_list,
        name="audit_report_list",
    ),
    path(
        "<uuid:opportunity_id>/audit_reports/<uuid:audit_report_id>/",
        views.audit_report_detail,
        name="audit_report_detail",
    ),
    path(
        "<uuid:opportunity_id>/audit_reports/<uuid:audit_report_id>/entries/<uuid:entry_id>/modal/",
        views.audit_report_task_modal,
        name="audit_report_task_modal",
    ),
    path(
        "<uuid:opportunity_id>/audit_reports/<uuid:audit_report_id>/entries/<uuid:entry_id>/action/",
        views.audit_report_task_action,
        name="audit_report_task_action",
    ),
    path(
        "<uuid:opportunity_id>/audit_reports/<uuid:audit_report_id>/complete/",
        views.audit_report_complete,
        name="audit_report_complete",
    ),
]
