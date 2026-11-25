import pytest
from allauth.socialaccount.models import SocialApp, SocialToken
from django.contrib.sites.models import Site
from django.test import RequestFactory

from commcare_connect.commcarehq_provider.views import CommcareHQOAuth2Adapter


@pytest.mark.django_db
def test_complete_login_sends_the_actual_access_token_string(httpx_mock):
    # allauth passes a SocialToken instance (not the raw string) to complete_login() since 65.x.
    # str(SocialToken(...)) is "social application token (None)", not the access token -- if
    # complete_login() ever formats `token` directly instead of `token.token`, this request never
    # matches and httpx_mock raises, catching the regression.
    app = SocialApp.objects.create(provider="commcarehq", name="HQ", client_id="id", secret="secret")
    app.sites.add(Site.objects.get_current())
    token = SocialToken(token="real-access-token-value")

    httpx_mock.add_response(
        url=CommcareHQOAuth2Adapter.profile_url,
        match_headers={"Authorization": "Bearer real-access-token-value"},
        json={"id": "123", "email": "a@b.com", "first_name": "A", "last_name": "B"},
    )

    request = RequestFactory().get("/")
    adapter = CommcareHQOAuth2Adapter(request)

    sociallogin = adapter.complete_login(request, app, token)

    assert sociallogin.account.uid == "123"
