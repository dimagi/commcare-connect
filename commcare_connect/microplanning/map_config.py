"""Config for the progress map's JS (``mapController``), rendered with ``json_script``."""

from django.middleware.csrf import get_token

# Always in the view context; a missing one raises rather than reaching the JS as `undefined`.
MAP_JS_CONTEXT_KEYS = [
    "edit_work_area_url",
    "user_visit_data_url",
    "review_inaccessibility_url",
    "attachment_url_base",
    "download_url",
    "bounds_url",
    "tiles_url",
    "visit_tiles_url",
    "groups_url",
    "implementation_areas_url",
    "work_area_detail_url",
    "inaccessible_mode",
    "assignment_mode",
    "max_autozoom_zoom",
    "workarea_min_zoom",
    "mapbox_api_key",
]

# Only in the context in assignment mode (from _get_assignment_mode_context).
MAP_JS_ASSIGNMENT_MODE_KEYS = [
    "group_work_areas_url",
    "flw_work_areas_url",
    "flw_summary_url",
    "assignment_save_url",
    "assignment_unassign_url",
]


def map_js_config(context, request):
    config = {key: context[key] for key in MAP_JS_CONTEXT_KEYS}
    config.update({key: context[key] for key in MAP_JS_ASSIGNMENT_MODE_KEYS if key in context})
    # CSRF_USE_SESSIONS is on, so there is no cookie for JS to read the token from.
    config["csrf_token"] = get_token(request)
    return config
