from django.urls import path

from commcare_connect.microplanning.views import (
    area_imports,
    assignment,
    coverage,
    home,
    inaccessibility,
    progress_map,
    work_area_groups,
    work_areas,
)

app_name = "microplanning"

urlpatterns = [
    path("<slug:opp_id>/", view=home.microplanning_home, name="microplanning_home"),
    path("<slug:opp_id>/coverage_progress/", coverage.coverage_progress, name="coverage_progress"),
    path("<slug:opp_id>/upload_work_areas/", area_imports.WorkAreaImport.as_view(), name="upload_work_areas"),
    path("<slug:opp_id>/import_status/", area_imports.import_status, name="import_status"),
    path(
        "<slug:opp_id>/upload_implementation_areas/",
        area_imports.ImplementationAreaImport.as_view(),
        name="upload_implementation_areas",
    ),
    path(
        "<slug:opp_id>/implementation_area_import_status/",
        area_imports.import_status,
        {"area_type": "implementation_area"},
        name="implementation_area_import_status",
    ),
    path(
        "<slug:opp_id>/clear_implementation_areas/",
        area_imports.clear_implementation_areas,
        name="clear_implementation_areas",
    ),
    path(
        "<slug:opp_id>/tiles/<int:z>/<int:x>/<int:y>/",
        progress_map.WorkAreaTileView.as_view(),
        name="workareas_tiles",
    ),
    path(
        "<slug:opp_id>/visit_tiles/<int:z>/<int:x>/<int:y>/",
        progress_map.UserVisitTileView.as_view(),
        name="user_visit_tiles",
    ),
    path(
        "<slug:opp_id>/workareas_group_geojson/",
        progress_map.workareas_group_geojson,
        name="workareas_group_geojson",
    ),
    path(
        "<slug:opp_id>/workareas_bounds/",
        progress_map.workareas_bounds,
        name="workareas_bounds",
    ),
    path("<slug:opp_id>/search_options/", progress_map.search_options, name="search_options"),
    path(
        "<slug:opp_id>/work_area_detail/<int:work_area_id>/",
        progress_map.work_area_detail_json,
        name="work_area_detail",
    ),
    path(
        "<slug:opp_id>/implementation_areas_geojson/",
        progress_map.implementation_areas_geojson,
        name="implementation_areas_geojson",
    ),
    path("<slug:opp_id>/cluster_work_areas/", work_area_groups.cluster_work_areas, name="cluster_work_areas"),
    path("<slug:opp_id>/clear_work_areas/", work_areas.clear_work_areas, name="clear_work_areas"),
    path(
        "<slug:opp_id>/clear_work_area_groups/", work_area_groups.clear_work_area_groups, name="clear_work_area_groups"
    ),
    path("<slug:opp_id>/clustering_status/", work_area_groups.clustering_status, name="clustering_status"),
    path(
        "<slug:opp_id>/modify_workarea/<int:work_area_id>/",
        work_areas.ModifyWorkAreaUpdateView.as_view(),
        name="modify_work_area",
    ),
    path("<slug:opp_id>/download_work_areas/", work_areas.download_work_areas, name="download_work_areas"),
    path("<slug:opp_id>/exclude_work_areas/", work_areas.exclude_work_areas, name="exclude_work_areas"),
    path(
        "<slug:opp_id>/review_inaccessibility/<int:work_area_id>/",
        inaccessibility.review_inaccessibility_request,
        name="review_inaccessibility_request",
    ),
    path(
        "<slug:opp_id>/review_inaccessibility/<int:work_area_id>/action/",
        inaccessibility.act_on_inaccessibility_request,
        name="act_on_inaccessibility_request",
    ),
    path(
        "<slug:opp_id>/assignment/group_work_areas/",
        assignment.get_work_areas_for_assignment,
        name="get_work_areas_for_assignment",
    ),
    path(
        "<slug:opp_id>/assignment/flw_work_areas/<int:assignee_id>/",
        assignment.get_flw_work_areas_for_assignment,
        name="get_flw_work_areas_for_assignment",
    ),
    path(
        "<slug:opp_id>/assignment/flw_summary/",
        assignment.get_flw_summary_for_assignment,
        name="get_flw_summary_for_assignment",
    ),
    path(
        "<slug:opp_id>/assignment/save/",
        assignment.save_assignment,
        name="save_assignment",
    ),
    path(
        "<slug:opp_id>/assignment/unassign/",
        assignment.unassign_work_areas,
        name="unassign_work_areas",
    ),
]
