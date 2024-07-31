from django.urls import path

from commcare_connect.reports import views

app_name = "reports"

urlpatterns = [
    path("delivery_stats", views.delivery_stats_report, name="delivery_stats_report"),
]
