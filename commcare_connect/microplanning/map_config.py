"""Config for the progress map's JS (``mapController``), rendered with ``json_script``."""

from dataclasses import asdict, dataclass, field, fields

from django.conf import settings
from django.middleware.csrf import get_token

from commcare_connect.microplanning.const import MAX_AUTOZOOM_ZOOM, WORKAREA_MIN_ZOOM


@dataclass(frozen=True)
class MapJsConfig:
    """Everything the map's JS reads from ``config``. Field names match the view's context variables.

    Fields without a default must be in the view context, so a missing one fails here rather than
    reaching the JS as ``undefined``. Defaults are only for values that don't depend on the request
    (constants and settings, which the view doesn't pass) and for the assignment-mode URLs.
    """

    edit_work_area_url: str
    user_visit_data_url: str
    review_inaccessibility_url: str
    attachment_url_base: str
    download_url: str
    bounds_url: str
    tiles_url: str
    visit_tiles_url: str
    groups_url: str
    implementation_areas_url: str
    work_area_detail_url: str
    inaccessible_mode: bool
    assignment_mode: bool
    # CSRF_USE_SESSIONS is on, so there is no cookie for JS to read the token from.
    csrf_token: str
    max_autozoom_zoom: int = MAX_AUTOZOOM_ZOOM
    workarea_min_zoom: int = WORKAREA_MIN_ZOOM
    mapbox_api_key: str | None = field(default_factory=lambda: settings.MAPBOX_TOKEN)
    # Only in the context in assignment mode (from _get_assignment_mode_context).
    group_work_areas_url: str | None = None
    flw_work_areas_url: str | None = None
    flw_summary_url: str | None = None
    assignment_save_url: str | None = None
    assignment_unassign_url: str | None = None


def map_js_config(context, request):
    values = {f.name: context[f.name] for f in fields(MapJsConfig) if f.name in context}
    values["csrf_token"] = get_token(request)
    return asdict(MapJsConfig(**values))
