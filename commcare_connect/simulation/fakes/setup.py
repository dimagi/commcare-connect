"""CommCare HQ stand-ins used while setting up programs and opportunities."""

from unittest import mock

from commcare_connect.opportunity.app_xml import AppNoBuildException
from commcare_connect.simulation.constants import DATA_DIR, SIM_DELIVER_APP_PREFIX, SIM_LEARN_APP_PREFIX

APP_FORM_DIRS = {
    SIM_LEARN_APP_PREFIX: DATA_DIR / "learn_app",
    SIM_DELIVER_APP_PREFIX: DATA_DIR / "deliver_app",
}


def patches():
    return [
        mock.patch("commcare_connect.opportunity.app_xml.get_form_xml_for_app", fake_get_form_xml_for_app),
    ]


def fake_get_form_xml_for_app(app):
    """Return the form XML HQ would put in the app's CCZ."""
    for prefix, form_dir in APP_FORM_DIRS.items():
        if app.cc_app_id.startswith(prefix):
            return [path.read_text() for path in sorted(form_dir.glob("*.xml"))]
    raise AppNoBuildException(f"App {app.cc_app_id} has no builds available.")
