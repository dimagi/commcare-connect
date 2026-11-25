"""CommCare HQ stand-ins used while setting up programs and opportunities."""

from unittest import mock

from commcare_connect.opportunity.app_xml import AppNoBuildException
from commcare_connect.simulation.constants import DATA_DIR, SIM_DELIVER_APP_PREFIX, SIM_LEARN_APP_PREFIX
from commcare_connect.utils.commcarehq_api import CommCareHQAccessDenied

APP_FORM_DIRS = {
    SIM_LEARN_APP_PREFIX: DATA_DIR / "learn_app",
    SIM_DELIVER_APP_PREFIX: DATA_DIR / "deliver_app",
}


# Every fakes module exposes this same patches() shape, so jscpd sees the modules as clones of each other.
# jscpd:ignore-start
def patches():
    return [
        mock.patch("commcare_connect.opportunity.app_xml.get_form_xml_for_app", fake_get_form_xml_for_app),
        mock.patch("commcare_connect.opportunity.forms.app_access_errors", fake_app_access_errors),
    ]


# jscpd:ignore-end


def fake_app_access_errors(api_key, apps):
    """Any key can fetch a simulated app; HQ answers any other app id with a 404, as it does for an unknown app."""
    return [
        None if _is_simulated_app(app_id) else CommCareHQAccessDenied(f"Failed to fetch app structure (404): {app_id}")
        for _domain, app_id in apps
    ]


def fake_get_form_xml_for_app(app):
    """Return the form XML HQ would put in the app's CCZ."""
    for prefix, form_dir in APP_FORM_DIRS.items():
        if app.cc_app_id.startswith(prefix):
            return [path.read_text() for path in sorted(form_dir.glob("*.xml"))]
    raise AppNoBuildException(f"App {app.cc_app_id} has no builds available.")


def _is_simulated_app(cc_app_id):
    return cc_app_id.startswith(tuple(APP_FORM_DIRS))
