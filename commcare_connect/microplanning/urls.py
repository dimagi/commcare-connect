from django.urls import path

from commcare_connect.microplanning import views

app_name = "microplanning"

urlpatterns = [
    path("<slug:opp_id>/", view=views.microplanning_home, name="microplanning_home"),
    path("<slug:opp_id>/coverage_progress/", views.coverage_progress, name="coverage_progress"),
    path("<slug:opp_id>/upload_work_areas/", views.WorkAreaImport.as_view(), name="upload_work_areas"),
    path("<slug:opp_id>/import_status/", views.import_status, name="import_status"),
    path(
        "<slug:opp_id>/upload_implementation_areas/",
        views.ImplementationAreaImport.as_view(),
        name="upload_implementation_areas",
    ),
    path(
        "<slug:opp_id>/implementation_area_import_status/",
        views.import_status,
        {"area_type": "implementation_area"},
        name="implementation_area_import_status",
    ),
    path(
        "<slug:opp_id>/clear_implementation_areas/",
        views.clear_implementation_areas,
        name="clear_implementation_areas",
    ),
    path(
        "<slug:opp_id>/tiles/<int:z>/<int:x>/<int:y>/",
        views.WorkAreaTileView.as_view(),
        name="workareas_tiles",
    ),
    path(
        "<slug:opp_id>/visit_tiles/<int:z>/<int:x>/<int:y>/",
        views.UserVisitTileView.as_view(),
        name="user_visit_tiles",
    ),
    path(
        "<slug:opp_id>/workareas_group_geojson/",
        views.workareas_group_geojson,
        name="workareas_group_geojson",
    ),
    path(
        "<slug:opp_id>/implementation_areas_geojson/",
        views.implementation_areas_geojson,
        name="implementation_areas_geojson",
    ),
    path("<slug:opp_id>/cluster_work_areas/", views.cluster_work_areas, name="cluster_work_areas"),
    path("<slug:opp_id>/clear_work_area_groups/", views.clear_work_area_groups, name="clear_work_area_groups"),
    path("<slug:opp_id>/clustering_status/", views.clustering_status, name="clustering_status"),
    path(
        "<slug:opp_id>/modify_workarea/<int:work_area_id>/",
        views.ModifyWorkAreaUpdateView.as_view(),
        name="modify_work_area",
    ),
    path("<slug:opp_id>/download_work_areas/", views.download_work_areas, name="download_work_areas"),
    path("<slug:opp_id>/exclude_work_areas/", views.exclude_work_areas, name="exclude_work_areas"),
    path(
        "<slug:opp_id>/review_inaccessibility/<int:work_area_id>/",
        views.review_inaccessibility_request,
        name="review_inaccessibility_request",
    ),
    path(
        "<slug:opp_id>/review_inaccessibility/<int:work_area_id>/action/",
        views.act_on_inaccessibility_request,
        name="act_on_inaccessibility_request",
    ),
    path(
        "<slug:opp_id>/assignment/group_work_areas/",
        views.get_work_areas_for_assignment,
        name="get_work_areas_for_assignment",
    ),
    path(
        "<slug:opp_id>/assignment/flw_work_areas/<int:assignee_id>/",
        views.get_flw_work_areas_for_assignment,
        name="get_flw_work_areas_for_assignment",
    ),
    path(
        "<slug:opp_id>/assignment/flw_summary/",
        views.get_flw_summary_for_assignment,
        name="get_flw_summary_for_assignment",
    ),
    path(
        "<slug:opp_id>/assignment/save/",
        views.save_assignment,
        name="save_assignment",
    ),
    path(
        "<slug:opp_id>/assignment/unassign/",
        views.unassign_work_areas,
        name="unassign_work_areas",
    ),
]
