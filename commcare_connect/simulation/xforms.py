"""Build learn and deliver form submissions and send them through the real form receiver code.

The payload shape is HQ's ``FormRepeater`` JSON, the same one ``form_receiver/tests/xforms.py`` builds; its stub
factories produce the Connect blocks so the XML-to-JSON conversion matches what HQ sends.
"""

import uuid
from datetime import UTC, datetime, timedelta

from commcare_connect.form_receiver.processor import process_xform
from commcare_connect.form_receiver.serializers import XFormSerializer
from commcare_connect.form_receiver.tests.xforms import (
    AssessmentStubFactory,
    DeliverUnitStubFactory,
    LearnModuleJsonFactory,
    get_form_json,
)

FORM_XMLNS = "http://openrosa.org/formdesigner/sim-form"


def submit_form(app, username, form_block, *, time_start, duration=timedelta(minutes=10), location=None):
    """Send one form for ``app`` from the CommCare user ``username`` through ``process_xform``.

    ``location`` is a CommCare GPS string (``"lat lon altitude accuracy"``); ``None`` submits without GPS.
    """
    form_json = build_form(app, username, form_block, time_start=time_start, duration=duration, location=location)
    serializer = XFormSerializer(data=form_json)
    serializer.is_valid(raise_exception=True)
    process_xform(serializer.save(), app.hq_server)
    return form_json


def build_form(app, username, form_block, *, time_start, duration, location):
    form_id = str(uuid.uuid4())
    metadata = {
        "@xmlns": "http://openrosa.org/jr/xforms",
        "app_build_version": 1,
        "deviceID": "simulation",
        "instanceID": form_id,
        "timeStart": _iso(time_start),
        "timeEnd": _iso(time_start + duration),
        "userID": uuid.uuid4().hex,
        "username": username,
        "location": location,
    }
    form_json = get_form_json(
        xmlns=FORM_XMLNS,
        form_block=form_block,
        id=form_id,
        domain=app.cc_domain,
        app_id=app.cc_app_id,
        build_id=uuid.uuid4().hex,
        received_on=_iso(time_start + duration + timedelta(minutes=1)),
        metadata=metadata,
    )
    form_json["form"]["meta"] = metadata
    return form_json


def learn_module_block(slug):
    """A learn-module block inside its question group, the way the learn app's forms carry it."""
    return {slug: LearnModuleJsonFactory(id=slug, name=_title(slug)).json}


def assessment_block(slug, score):
    return {slug: AssessmentStubFactory(id=slug, score=score).json}


def deliver_block(slug, entity_id, entity_name):
    return {
        slug: DeliverUnitStubFactory(id=slug, name=_title(slug), entity_id=entity_id, entity_name=entity_name).json
    }


def gps(lat, lon, accuracy=10):
    return f"{lat:.6f} {lon:.6f} 0.0 {accuracy}"


def _iso(value: datetime):
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _title(slug):
    return slug.replace("_", " ").title()
