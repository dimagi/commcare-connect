from django.urls import path

from commcare_connect.microplanning import views

app_name = "microplanning"

urlpatterns = [
    path("<slug:opp_id>/", view=views.microplanning_home, name="microplanning_home"),
    path("<slug:opp_id>/upload_work_areas/", views.WorkAreaImport.as_view(), name="upload_work_areas"),
    path("<slug:opp_id>/import_status/", views.import_status, name="import_status"),
]
