import pytest

from commcare_connect.microplanning.map_config import (
    MAP_JS_ASSIGNMENT_MODE_KEYS,
    MAP_JS_CONTEXT_KEYS,
    map_js_config,
)


def _context(keys):
    return {key: f"value-of-{key}" for key in keys}


@pytest.mark.parametrize(
    "context_keys",
    [
        pytest.param(MAP_JS_CONTEXT_KEYS + MAP_JS_ASSIGNMENT_MODE_KEYS, id="assignment-mode"),
        pytest.param(MAP_JS_CONTEXT_KEYS, id="other-modes"),
    ],
)
def test_copies_only_the_keys_the_context_has(rf, context_keys):
    context = _context(context_keys)
    context["opportunity"] = "not for the JS"

    config = map_js_config(context, rf.get("/"))

    assert {key: config[key] for key in context_keys} == {key: context[key] for key in context_keys}
    assert set(config) == set(context_keys) | {"csrf_token"}


def test_missing_required_key_raises(rf):
    context = _context(MAP_JS_CONTEXT_KEYS)
    del context["attachment_url_base"]

    with pytest.raises(KeyError, match="attachment_url_base"):
        map_js_config(context, rf.get("/"))


def test_includes_a_csrf_token(rf):
    assert map_js_config(_context(MAP_JS_CONTEXT_KEYS), rf.get("/"))["csrf_token"]


def test_missing_mapbox_token_is_passed_through(rf):
    """MAPBOX_TOKEN defaults to None; the JS treats a falsy token as "no map" rather than failing here."""
    context = _context(MAP_JS_CONTEXT_KEYS) | {"mapbox_api_key": None}
    assert map_js_config(context, rf.get("/"))["mapbox_api_key"] is None
