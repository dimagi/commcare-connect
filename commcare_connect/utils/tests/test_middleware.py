import pytest
from django.contrib.messages.middleware import MessageMiddleware
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpResponseRedirect

from commcare_connect.utils.middleware import XSS_PROTECTION_HEADER_VALUE, CustomErrorHandlingMiddleware
from commcare_connect.utils.oauth_tokens import SocialTokenMissingError


def _request_with_messages(rf, **extra):
    request = rf.get("/a/org/opportunity/", **extra)
    SessionMiddleware(lambda r: None).process_request(request)
    MessageMiddleware(lambda r: None).process_request(request)
    return request


@pytest.mark.django_db
def test_missing_ocs_token_redirects_to_root_without_referer(rf):
    request = _request_with_messages(rf)
    middleware = CustomErrorHandlingMiddleware(lambda r: None)

    response = middleware.process_exception(request, SocialTokenMissingError("no token"))

    assert isinstance(response, HttpResponseRedirect)
    assert response.url == "/"


@pytest.mark.django_db
def test_offsite_referer_is_rejected(rf):
    request = _request_with_messages(rf, HTTP_REFERER="https://evil.example.com/phish")
    middleware = CustomErrorHandlingMiddleware(lambda r: None)

    response = middleware.process_exception(request, SocialTokenMissingError("no token"))

    assert response.url == "/"


@pytest.mark.django_db
def test_same_host_absolute_referer_is_honored(rf):
    request = _request_with_messages(rf, HTTP_REFERER="http://testserver/a/org/opportunity/")
    middleware = CustomErrorHandlingMiddleware(lambda r: None)

    response = middleware.process_exception(request, SocialTokenMissingError("no token"))

    assert response.url == "http://testserver/a/org/opportunity/"


@pytest.mark.django_db
def test_xss_protection_header(client):
    response = client.get("/accounts/login/")

    assert response.headers["X-XSS-Protection"] == XSS_PROTECTION_HEADER_VALUE


@pytest.mark.django_db
def test_hsts_header(client, settings):
    settings.SECURE_HSTS_SECONDS = 60
    settings.SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    settings.SECURE_HSTS_PRELOAD = True

    response = client.get("/accounts/login/")

    assert response.headers["Strict-Transport-Security"] == "max-age=60; includeSubDomains; preload"


@pytest.mark.django_db
def test_no_store_for_logged_in_user(client, user):
    client.force_login(user)

    response = client.get("/accounts/email/")

    assert "no-store" in response.headers["Cache-Control"]
    assert response.headers["Pragma"] == "no-cache"
