from dataclasses import MISSING, fields

import pytest

from commcare_connect.microplanning.const import MAX_AUTOZOOM_ZOOM, WORKAREA_MIN_ZOOM
from commcare_connect.microplanning.map_config import MapJsConfig, map_js_config

REQUIRED = [
    f.name
    for f in fields(MapJsConfig)
    if f.default is MISSING and f.default_factory is MISSING and f.name != "csrf_token"
]
ASSIGNMENT_MODE_URLS = [
    "group_work_areas_url",
    "flw_work_areas_url",
    "flw_summary_url",
    "assignment_save_url",
    "assignment_unassign_url",
]


def _context(keys):
    return {key: f"value-of-{key}" for key in keys}


@pytest.mark.parametrize(
    "context_keys, assignment_mode",
    [
        pytest.param(REQUIRED + ASSIGNMENT_MODE_URLS, True, id="assignment-mode"),
        pytest.param(REQUIRED, False, id="other-modes"),
    ],
)
def test_copies_the_context_values(rf, context_keys, assignment_mode):
    context = _context(context_keys)
    context["opportunity"] = "not for the JS"

    config = map_js_config(context, rf.get("/"))

    assert {key: config[key] for key in context_keys} == {key: context[key] for key in context_keys}
    assert "opportunity" not in config
    expected_assignment_urls = {key: context[key] if assignment_mode else None for key in ASSIGNMENT_MODE_URLS}
    assert {key: config[key] for key in ASSIGNMENT_MODE_URLS} == expected_assignment_urls


def test_missing_required_key_raises(rf):
    context = _context(REQUIRED)
    del context["attachment_url_base"]

    with pytest.raises(TypeError, match="attachment_url_base"):
        map_js_config(context, rf.get("/"))


def test_includes_a_csrf_token(rf):
    assert map_js_config(_context(REQUIRED), rf.get("/"))["csrf_token"]


def test_zoom_levels_come_from_the_constants(rf):
    config = map_js_config(_context(REQUIRED), rf.get("/"))

    assert config["workarea_min_zoom"] == WORKAREA_MIN_ZOOM
    assert config["max_autozoom_zoom"] == MAX_AUTOZOOM_ZOOM


@pytest.mark.parametrize("token", ["test-mapbox-token", None])
def test_mapbox_token_comes_from_settings(rf, settings, token):
    """MAPBOX_TOKEN defaults to None; the JS treats a falsy token as "no map" rather than failing here."""
    settings.MAPBOX_TOKEN = token

    assert map_js_config(_context(REQUIRED), rf.get("/"))["mapbox_api_key"] == token
