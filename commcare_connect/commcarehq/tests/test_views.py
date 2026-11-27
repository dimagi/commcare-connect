import json
import logging
from unittest.mock import patch

import pytest
from django.test import RequestFactory

from commcare_connect.commcarehq.views import (
    API_KEY_NOT_FOUND_CHOICE,
    _get_api_key,
    application_options,
    domain_options,
    options_for_api_key,
)
from commcare_connect.opportunity.tests.factories import HQApiKeyFactory, OpportunityFactory
from commcare_connect.utils.commcarehq_api import CommCareHQAccessDenied, CommCareHQAPIException

APP_1 = {"id": "app1", "name": "App 1"}
APP_2 = {"id": "app2", "name": "App 2"}


@pytest.fixture
def api_key(db):
    return HQApiKeyFactory()


def _single_message(options):
    [(value, label)] = options
    assert value == ""
    return label


@pytest.mark.parametrize(
    "error, message",
    [
        (CommCareHQAccessDenied("401"), "refused this API key"),
        (CommCareHQAPIException("500"), "Couldn't load domains"),
    ],
)
def test_domain_options_on_hq_error(api_key, error, message):
    with patch("commcare_connect.commcarehq.views.get_domains_for_user", side_effect=error):
        assert message in _single_message(domain_options(api_key))


def test_domain_options_when_key_sees_no_domains(api_key):
    with patch("commcare_connect.commcarehq.views.get_domains_for_user", return_value=[]):
        assert "No project spaces" in _single_message(domain_options(api_key))


def test_domain_options_lists_domains(api_key):
    with patch("commcare_connect.commcarehq.views.get_domains_for_user", return_value=["dom1", "dom2"]):
        assert domain_options(api_key)[1:] == [("dom1", "dom1"), ("dom2", "dom2")]


@pytest.mark.parametrize(
    "error, message",
    [
        (CommCareHQAccessDenied("403"), "refused access to this project space's apps"),
        (CommCareHQAPIException("no answer within 25s"), "Couldn't load apps"),
    ],
)
def test_application_options_on_hq_error(api_key, error, message):
    with patch("commcare_connect.commcarehq.views.get_applications_for_user_by_domain", side_effect=error):
        assert message in _single_message(application_options(api_key, "dom"))


def test_application_options_when_space_has_no_apps(api_key):
    with patch("commcare_connect.commcarehq.views.get_applications_for_user_by_domain", return_value=[]):
        assert "No apps found" in _single_message(application_options(api_key, "dom"))


def test_application_options_leaves_out_apps_used_by_active_opportunities(api_key):
    OpportunityFactory(active=True, learn_app__cc_domain="dom", learn_app__cc_app_id="app1")
    with patch("commcare_connect.commcarehq.views.get_applications_for_user_by_domain", return_value=[APP_1, APP_2]):
        assert application_options(api_key, "dom")[1:] == [(json.dumps(APP_2), "App 2")]


def test_application_options_when_every_app_is_used(api_key):
    OpportunityFactory(active=True, learn_app__cc_domain="dom", learn_app__cc_app_id="app1")
    with patch("commcare_connect.commcarehq.views.get_applications_for_user_by_domain", return_value=[APP_1]):
        assert "already used" in _single_message(application_options(api_key, "dom"))


def test_options_for_api_key_when_key_is_not_the_users(api_key, user):
    request = RequestFactory().get("/")
    request.user = user  # not the key's owner
    options = options_for_api_key(request, api_key.hq_server_id, api_key.pk, lambda key: pytest.fail("built options"))
    assert options == [API_KEY_NOT_FOUND_CHOICE]


def test_options_for_api_key_builds_options_for_the_users_key(api_key):
    request = RequestFactory().get("/")
    request.user = api_key.user
    assert options_for_api_key(request, api_key.hq_server_id, api_key.pk, lambda key: [("v", key.pk)]) == [
        ("v", api_key.pk)
    ]


@pytest.mark.parametrize(
    "error, level", [(CommCareHQAccessDenied("401"), logging.WARNING), (CommCareHQAPIException("500"), logging.ERROR)]
)
def test_hq_failures_log_at_error_and_refusals_at_warning(api_key, caplog, error, level):
    """Sentry only records errors: a refused key is the user's to fix, HQ failing is ours to hear about."""
    with patch("commcare_connect.commcarehq.views.get_domains_for_user", side_effect=error):
        domain_options(api_key)
    assert [record.levelno for record in caplog.records] == [level]


@pytest.mark.parametrize("hq_server, api_key_id", [("1", "abc"), ("abc", "1")])
def test_get_api_key_when_an_id_is_not_a_number(db, user, hq_server, api_key_id):
    request = RequestFactory().get("/")
    request.user = user
    assert _get_api_key(request, hq_server, api_key_id) is None


def test_get_api_key_returns_the_users_key(api_key):
    request = RequestFactory().get("/")
    request.user = api_key.user
    assert _get_api_key(request, api_key.hq_server_id, api_key.pk) == api_key
