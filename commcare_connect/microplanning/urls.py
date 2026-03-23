from django.urls import path

from commcare_connect.microplanning import views

app_name = "microplanning"

urlpatterns = [
    path("<slug:opp_id>/", view=views.microplanning_home, name="microplanning_home"),
    path("<slug:opp_id>/upload_work_areas/", views.WorkAreaImport.as_view(), name="upload_work_areas"),
    path("<slug:opp_id>/import_status/", views.import_status, name="import_status"),
    path(
        "<slug:opp_id>/tiles/<int:z>/<int:x>/<int:y>/",
        views.WorkAreaTileView.as_view(),
        name="workareas_tiles",
    ),
    path(
        "<slug:opp_id>/workareas_group_geojson/",
        views.workareas_group_geojson,
        name="workareas_group_geojson",
    ),
    path("<slug:opp_id>/cluster_work_areas/", views.cluster_work_areas, name="cluster_work_areas"),
    path("<slug:opp_id>/clustering_status/", views.clustering_status, name="clustering_status"),
    path(
        "<slug:opp_id>/modify_workarea/<int:work_area_id>/",
        views.ModifyWorkAreaUpdateView.as_view(),
        name="modify_work_area",
    ),
]
