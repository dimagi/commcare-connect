import pytest
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
@pytest.mark.parametrize(
    "referer,expected_url",
    [
        pytest.param(None, "/", id="no_referer_redirects_to_root"),
        pytest.param("https://evil.example.com/phish", "/", id="offsite_referer_is_rejected"),
        pytest.param(
            "http://testserver/a/org/opportunity/",
            "http://testserver/a/org/opportunity/",
            id="same_host_absolute_referer_is_honored",
        ),
    ],
)
def test_missing_ocs_token_redirect(rf, referer, expected_url):
    extra = {"HTTP_REFERER": referer} if referer else {}
    request = _request_with_messages(rf, **extra)
    middleware = CustomErrorHandlingMiddleware(lambda r: None)

    response = middleware.process_exception(request, SocialTokenMissingError("no token"))

    assert isinstance(response, HttpResponseRedirect)
    assert response.url == expected_url
