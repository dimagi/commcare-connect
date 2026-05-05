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
]
