from django.urls import path

from commcare_connect.reports import views

app_name = "reports"

urlpatterns = [
    path("delivery_stats", view=views.DeliveryStatsReportView.as_view(), name="delivery_stats_report"),
    path("invoice_report", view=views.InvoiceReportView.as_view(), name="invoice_report"),
    path("export_invoice_report", view=views.export_invoice_report, name="export_invoice_report"),
    path("export_status/<slug:task_id>", view=views.export_status, name="export_status"),
    path("download_export/<slug:task_id>", view=views.download_export, name="download_export"),
]
