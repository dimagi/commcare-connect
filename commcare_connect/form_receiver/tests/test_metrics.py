from unittest import mock

import pytest
from django.utils.timezone import now
from rest_framework.test import APIClient

from commcare_connect.form_receiver.exceptions import ProcessingError
from commcare_connect.form_receiver.metrics import (
    END_TO_END_TIME_METRIC,
    FORMS_METRIC,
    PROCESSING_TIME_METRIC,
    FormMetrics,
)
from commcare_connect.form_receiver.tests.xforms import DeliverUnitStubFactory, get_form_json
from commcare_connect.opportunity.models import Opportunity
from commcare_connect.opportunity.tests.factories import DeliverUnitFactory
from commcare_connect.users.models import User
from commcare_connect.utils.tests.api import add_token_credentials


@pytest.fixture
def sentry_metrics():
    with mock.patch("commcare_connect.form_receiver.metrics.metrics") as sentry_metrics:
        yield sentry_metrics


@pytest.fixture
def deliver_form_json(opportunity: Opportunity):
    deliver_unit = DeliverUnitFactory(app=opportunity.deliver_app, payment_unit=opportunity.paymentunit_set.first())
    return get_form_json(
        form_block=DeliverUnitStubFactory(id=deliver_unit.slug).json,
        domain=deliver_unit.app.cc_domain,
        app_id=deliver_unit.app.cc_app_id,
    )


@pytest.fixture
def post_form(
    api_client: APIClient,
    mobile_user_with_connect_link: User,
    opportunity: Opportunity,
    django_capture_on_commit_callbacks,
):
    """POST a form to the receiver as HQ, running the on-commit callbacks. Unhandled errors return a 500."""
    add_token_credentials(
        api_client=api_client,
        user=mobile_user_with_connect_link,
        application=opportunity.hq_server.oauth_application,
    )
    api_client.raise_request_exception = False

    def post(form_json):
        with django_capture_on_commit_callbacks(execute=True):
            return api_client.post("/api/receiver/", data=form_json, format="json")

    return post


def _assert_recorded(sentry_metrics, outcome, app_type="none", distributions=(PROCESSING_TIME_METRIC,)):
    sentry_metrics.count.assert_called_once_with(
        FORMS_METRIC, 1, attributes={"outcome": outcome, "app_type": app_type}
    )
    assert {call.args[0] for call in sentry_metrics.distribution.call_args_list} == set(distributions)


@pytest.mark.django_db
def test_processed_form(post_form, deliver_form_json, sentry_metrics):
    response = post_form(deliver_form_json)

    assert response.status_code == 200
    _assert_recorded(sentry_metrics, "processed", "deliver", (PROCESSING_TIME_METRIC, END_TO_END_TIME_METRIC))


@pytest.mark.django_db
def test_duplicate_form(post_form, deliver_form_json, sentry_metrics):
    post_form(deliver_form_json)
    sentry_metrics.reset_mock()

    response = post_form(deliver_form_json)

    assert response.status_code == 200
    _assert_recorded(sentry_metrics, "duplicate")


@pytest.mark.django_db
def test_form_for_app_without_opportunity(post_form, deliver_form_json, sentry_metrics):
    response = post_form({**deliver_form_json, "app_id": "not-a-connect-app"})

    assert response.status_code == 200
    _assert_recorded(sentry_metrics, "ignored")


@pytest.mark.django_db
@pytest.mark.parametrize(
    "failure",
    [(ProcessingError("missing claim"), "rejected"), (RuntimeError("bug"), "error")],
    ids=["rejected", "error"],
)
def test_failed_form(post_form, deliver_form_json, sentry_metrics, failure):
    side_effect, outcome = failure
    with mock.patch("commcare_connect.form_receiver.views.process_xform", side_effect=side_effect):
        response = post_form(deliver_form_json)

    assert response.status_code in (400, 500)
    _assert_recorded(sentry_metrics, outcome)


@pytest.mark.django_db
def test_invalid_payload_is_rejected(post_form, sentry_metrics):
    response = post_form({"foo": "bar"})

    assert response.status_code == 400
    _assert_recorded(sentry_metrics, "rejected")


def test_recording_without_sentry_configured():
    """With no DSN the Sentry calls do nothing, so the receiver works unchanged."""
    FormMetrics(received_on=now(), app_type="learn")._record("processed")


@pytest.mark.django_db
def test_metrics_failure_does_not_change_response(post_form, deliver_form_json, sentry_metrics):
    sentry_metrics.count.side_effect = Exception("Sentry is down")

    response = post_form(deliver_form_json)

    assert response.status_code == 200
