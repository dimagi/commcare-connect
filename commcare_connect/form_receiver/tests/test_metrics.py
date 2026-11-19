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
def receiver_client(api_client: APIClient, mobile_user_with_connect_link: User, opportunity: Opportunity):
    add_token_credentials(
        api_client=api_client,
        user=mobile_user_with_connect_link,
        application=opportunity.hq_server.oauth_application,
    )
    return api_client


def _post(client, form_json, capture_on_commit):
    with capture_on_commit(execute=True):
        return client.post("/api/receiver/", data=form_json, format="json")


def _assert_recorded(sentry_metrics, outcome, app_type="none", distributions=(PROCESSING_TIME_METRIC,)):
    sentry_metrics.count.assert_called_once_with(
        FORMS_METRIC, 1, attributes={"outcome": outcome, "app_type": app_type}
    )
    assert {call.args[0] for call in sentry_metrics.distribution.call_args_list} == set(distributions)


@pytest.mark.django_db
def test_processed_form(receiver_client, deliver_form_json, sentry_metrics, django_capture_on_commit_callbacks):
    response = _post(receiver_client, deliver_form_json, django_capture_on_commit_callbacks)

    assert response.status_code == 200
    _assert_recorded(sentry_metrics, "processed", "deliver", (PROCESSING_TIME_METRIC, END_TO_END_TIME_METRIC))


@pytest.mark.django_db
def test_duplicate_form(receiver_client, deliver_form_json, sentry_metrics, django_capture_on_commit_callbacks):
    _post(receiver_client, deliver_form_json, django_capture_on_commit_callbacks)
    sentry_metrics.reset_mock()

    response = _post(receiver_client, deliver_form_json, django_capture_on_commit_callbacks)

    assert response.status_code == 200
    _assert_recorded(sentry_metrics, "duplicate")


@pytest.mark.django_db
def test_form_for_app_without_opportunity(
    receiver_client, deliver_form_json, sentry_metrics, django_capture_on_commit_callbacks
):
    form_json = {**deliver_form_json, "app_id": "not-a-connect-app"}
    response = _post(receiver_client, form_json, django_capture_on_commit_callbacks)

    assert response.status_code == 200
    _assert_recorded(sentry_metrics, "ignored")


@pytest.mark.django_db
@pytest.mark.parametrize(
    "side_effect, outcome",
    [(ProcessingError("missing claim"), "rejected"), (RuntimeError("bug"), "error")],
)
def test_failed_form(
    receiver_client, deliver_form_json, sentry_metrics, django_capture_on_commit_callbacks, side_effect, outcome
):
    receiver_client.raise_request_exception = False
    with mock.patch("commcare_connect.form_receiver.views.process_xform", side_effect=side_effect):
        response = _post(receiver_client, deliver_form_json, django_capture_on_commit_callbacks)

    assert response.status_code in (400, 500)
    _assert_recorded(sentry_metrics, outcome)


@pytest.mark.django_db
def test_invalid_payload_is_rejected(receiver_client, sentry_metrics, django_capture_on_commit_callbacks):
    response = _post(receiver_client, {"foo": "bar"}, django_capture_on_commit_callbacks)

    assert response.status_code == 400
    _assert_recorded(sentry_metrics, "rejected")


def test_recording_without_sentry_configured():
    """With no DSN the Sentry calls do nothing, so the receiver works unchanged."""
    FormMetrics(received_on=now(), app_type="learn")._record("processed")


@pytest.mark.django_db
def test_metrics_failure_does_not_change_response(
    receiver_client, deliver_form_json, sentry_metrics, django_capture_on_commit_callbacks
):
    sentry_metrics.count.side_effect = Exception("Sentry is down")

    response = _post(receiver_client, deliver_form_json, django_capture_on_commit_callbacks)

    assert response.status_code == 200
