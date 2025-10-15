from django.urls import path

from commcare_connect.reports import views
from commcare_connect.reports.dashboard.views import UserVisitDashboardView

app_name = "reports"

urlpatterns = [
    path("program_dashboard", views.program_dashboard_report, name="program_dashboard_report"),
    path("delivery_stats", view=views.DeliveryStatsReportView.as_view(), name="delivery_stats_report"),
    path("api/visit_map_data/", views.visit_map_data, name="visit_map_data"),
    path("api/dashboard_stats/", views.dashboard_stats_api, name="dashboard_stats_api"),
    path("api/dashboard_charts/", views.dashboard_charts_api, name="dashboard_charts_api"),
    path("user_visits/", UserVisitDashboardView.as_view(), name="user_visit_dashboard"),
]
