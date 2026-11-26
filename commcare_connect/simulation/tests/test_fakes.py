from unittest import mock

import pytest
from django.test import RequestFactory

from commcare_connect.opportunity.app_xml import AppNoBuildException
from commcare_connect.opportunity.models import DeliverUnit, LearnModule
from commcare_connect.opportunity.tasks import sync_learn_modules_and_deliver_units
from commcare_connect.opportunity.tests.factories import CommCareAppFactory, OpportunityFactory
from commcare_connect.simulation.constants import (
    DELIVER_UNIT_SLUGS,
    LEARN_MODULE_SLUGS,
    SIM_DELIVER_APP_PREFIX,
    SIM_LEARN_APP_PREFIX,
)
from commcare_connect.simulation.fakes import local_fakes
from commcare_connect.simulation.fakes.setup import fake_check_api_key_access, fake_get_form_xml_for_app
from commcare_connect.utils.analytics import Event, send_bulk_events_to_ga
from commcare_connect.utils.commcarehq_api import CommCareHQAccessDenied, app_path


@pytest.mark.django_db
def test_app_sync_creates_simulated_learn_modules_and_deliver_units():
    opportunity = OpportunityFactory(
        learn_app=CommCareAppFactory(cc_app_id=f"{SIM_LEARN_APP_PREFIX}1"),
        deliver_app=CommCareAppFactory(cc_app_id=f"{SIM_DELIVER_APP_PREFIX}1"),
    )

    with local_fakes():
        sync_learn_modules_and_deliver_units(opportunity)

    assert _slugs(LearnModule, opportunity.learn_app) == set(LEARN_MODULE_SLUGS)
    assert _slugs(DeliverUnit, opportunity.deliver_app) == set(DELIVER_UNIT_SLUGS)


@pytest.mark.django_db
def test_unknown_app_has_no_build():
    with pytest.raises(AppNoBuildException):
        fake_get_form_xml_for_app(CommCareAppFactory(cc_app_id="some-real-hq-app"))


def test_only_simulated_apps_are_accessible():
    learn_error, deliver_error, unknown_error = fake_check_api_key_access(
        None,
        [
            app_path("sim-domain", f"{SIM_LEARN_APP_PREFIX}1"),
            app_path("sim-domain", f"{SIM_DELIVER_APP_PREFIX}1"),
            app_path("sim-domain", "some-real-hq-app"),
        ],
    )

    assert learn_error is None
    assert deliver_error is None
    assert isinstance(unknown_error, CommCareHQAccessDenied)


@pytest.mark.django_db
def test_analytics_events_are_not_sent(settings, user):
    settings.GA_MEASUREMENT_ID = "G-SIMULATED"
    settings.GA_API_SECRET = "secret"
    request = RequestFactory().get("/")
    request.user = user

    with local_fakes(), mock.patch("commcare_connect.utils.analytics.httpx") as httpx:
        send_bulk_events_to_ga(request, [Event("visit_approved", {})])

    httpx.post.assert_not_called()


def _slugs(model, app):
    return set(model.objects.filter(app=app).values_list("slug", flat=True))
