from datetime import UTC, datetime, timedelta

import pytest

from commcare_connect.form_receiver.serializers import XFormSerializer
from commcare_connect.opportunity.tests.factories import CommCareAppFactory
from commcare_connect.simulation.xforms import build_form, deliver_block, gps


@pytest.mark.django_db
def test_built_form_passes_the_receiver_serializer():
    app = CommCareAppFactory(cc_domain="sim-domain", cc_app_id="sim-deliver-1")
    time_start = datetime(2026, 1, 5, 9, 30, tzinfo=UTC)

    form_json = build_form(
        app,
        "worker@sim-domain.commcarehq.org",
        deliver_block("household_visit", "entity-1", "Household 1"),
        time_start=time_start,
        duration=timedelta(minutes=12),
        location=gps(12.5, 7.25),
    )

    xform = _parse(form_json)

    assert (xform.app_id, xform.metadata.duration, xform.metadata.location) == (
        "sim-deliver-1",
        timedelta(minutes=12),
        "12.500000 7.250000 0.0 10",
    )
    assert xform.form["household_visit"]["deliver"]["@id"] == "household_visit"


def _parse(form_json):
    serializer = XFormSerializer(data=form_json)
    serializer.is_valid(raise_exception=True)
    return serializer.save()
