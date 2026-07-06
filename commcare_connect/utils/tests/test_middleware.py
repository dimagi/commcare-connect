import pytest
from django.contrib.messages import get_messages
from django.contrib.messages.middleware import MessageMiddleware
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpResponseRedirect

from commcare_connect.utils.middleware import CustomErrorHandlingMiddleware
from commcare_connect.utils.oauth_tokens import SocialTokenMissingError


def _request_with_messages(rf, **extra):
    request = rf.get("/a/org/opportunity/", **extra)
    SessionMiddleware(lambda r: None).process_request(request)
    MessageMiddleware(lambda r: None).process_request(request)
    return request


@pytest.mark.django_db
def test_missing_ocs_token_redirects_with_connect_message(rf):
    request = _request_with_messages(rf, HTTP_REFERER="/a/org/opportunity/")
    middleware = CustomErrorHandlingMiddleware(lambda r: None)

    response = middleware.process_exception(request, SocialTokenMissingError("no token"))

    assert isinstance(response, HttpResponseRedirect)
    assert response.url == "/a/org/opportunity/"
    text = " ".join(str(m) for m in get_messages(request))
    assert "/accounts/ocs/login/" in text
    assert "process=connect" in text
    assert "next=" in text


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
