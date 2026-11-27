"""CommCare HQ stand-ins used while setting up programs and opportunities."""

import re
from unittest import mock

from commcare_connect.opportunity.app_xml import AppNoBuildException
from commcare_connect.simulation.constants import DATA_DIR, SIM_DELIVER_APP_PREFIX, SIM_LEARN_APP_PREFIX
from commcare_connect.utils.commcarehq_api import CommCareHQAccessDenied

APP_FORM_DIRS = {
    SIM_LEARN_APP_PREFIX: DATA_DIR / "learn_app",
    SIM_DELIVER_APP_PREFIX: DATA_DIR / "deliver_app",
}
# The path `app_path` builds.
APP_PATH = re.compile(r"/a/[^/]+/api/v0\.5/application/(?P<app_id>[^/]+)/")


# Every fakes module exposes this same patches() shape, so jscpd sees the modules as clones of each other.
# jscpd:ignore-start
def patches():
    return [
        mock.patch("commcare_connect.opportunity.app_xml.get_form_xml_for_app", fake_get_form_xml_for_app),
        mock.patch("commcare_connect.opportunity.forms.check_api_key_access", fake_check_api_key_access),
    ]


# jscpd:ignore-end


def fake_check_api_key_access(api_key, paths):
    """Any key can GET a simulated app or any other HQ path; HQ answers any other app with a 404, as it does for an
    unknown app."""
    return [
        CommCareHQAccessDenied(f"Failed to check access to {path} (404)") if _is_unknown_app_path(path) else None
        for path in paths
    ]


def fake_get_form_xml_for_app(app):
    """Return the form XML HQ would put in the app's CCZ."""
    for prefix, form_dir in APP_FORM_DIRS.items():
        if app.cc_app_id.startswith(prefix):
            return [path.read_text() for path in sorted(form_dir.glob("*.xml"))]
    raise AppNoBuildException(f"App {app.cc_app_id} has no builds available.")


def _is_simulated_app(cc_app_id):
    return cc_app_id.startswith(tuple(APP_FORM_DIRS))


def _is_unknown_app_path(path):
    match = APP_PATH.fullmatch(path)
    return bool(match) and not _is_simulated_app(match["app_id"])
