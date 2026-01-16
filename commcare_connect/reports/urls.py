from django.urls import path

from commcare_connect.reports import views

app_name = "reports"

urlpatterns = [
    path("delivery_stats", view=views.DeliveryStatsReportView.as_view(), name="delivery_stats_report"),
    path("invoice_report", view=views.InvoiceReportView.as_view(), name="invoice_report"),
]
