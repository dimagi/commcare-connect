from django.urls import path

from commcare_connect.reports import views

app_name = "reports"

urlpatterns = [
    path("reports/admin", views.admin_report, name="admin_report"),
]
