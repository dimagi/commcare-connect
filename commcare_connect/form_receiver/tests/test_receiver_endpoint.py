import datetime
from unittest import mock

from django.utils.timezone import now
from rest_framework.test import APIClient, APIRequestFactory

from commcare_connect.form_receiver.exceptions import ProcessingError
from commcare_connect.form_receiver.tests.xforms import get_form_json
from commcare_connect.form_receiver.views import FormReceiver
from commcare_connect.users.models import User
from commcare_connect.users.tests.factories import HQServerFactory

receiver_view = FormReceiver.as_view()


def test_form_receiver_requires_auth(api_rf: APIRequestFactory):
    request = api_rf.post("/api/receiver/", data={"foo": "bar"}, format="json")
    response = receiver_view(request)
    assert response.status_code == 401


def test_form_receiver_only_accept_json(user: User, api_client: APIClient):
    add_credentials(api_client, user)
    response = api_client.post("/api/receiver/", data={"foo": "bar"})
    assert response.status_code == 415


def test_form_receiver_validation(user: User, api_client: APIClient):
    add_credentials(api_client, user)
    response = api_client.post("/api/receiver/", data={"foo": "bar"}, format="json")
    assert response.status_code == 400
    assert set(response.data) == {"domain", "metadata", "build_id", "app_id", "received_on", "form", "id"}


def test_process_xform_error(user: User, api_client: APIClient):
    hq_server = HQServerFactory()
    oauth_application = hq_server.oauth_application
    add_credentials(api_client, user, oauth_application=oauth_application)
    with (mock.patch("commcare_connect.form_receiver.views.process_xform") as process_xform,):
        process_xform.side_effect = ProcessingError("oops, something went wrong")
        response = api_client.post("/api/receiver/", data=get_form_json(), format="json")
    assert response.status_code == 400, response.data
    assert response.data == {"detail": "oops, something went wrong"}


def test_processor_view_hq_server_not_found_error(user: User, api_client: APIClient):
    add_credentials(api_client, user)
    response = api_client.post("/api/receiver/", data=get_form_json(), format="json")
    assert response.status_code == 400, response.data
    assert response.data == {"detail": "A server error occurred."}


def add_credentials(api_client: APIClient, user: User, oauth_application=None):
    token, _ = user.oauth2_provider_accesstoken.get_or_create(
        token="token",
        scope="read write",
        defaults={"expires": now() + datetime.timedelta(hours=1)},
        application=oauth_application,
    )
    api_client.credentials(Authorization=f"Bearer {token}")
