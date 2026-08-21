import pytest
from allauth.socialaccount.models import SocialToken

from commcare_connect.ocs_provider.views import OcsOAuth2Adapter
from commcare_connect.users.tests.test_connections import _create_social_app


@pytest.mark.django_db
def test_complete_login_sends_the_actual_access_token_string(httpx_mock, rf):
    # allauth passes a SocialToken instance (not the raw string) to complete_login() since 65.x.
    # str(SocialToken(...)) is "social application token (None)", not the access token -- if
    # complete_login() ever formats `token` directly instead of `token.token`, this request never
    # matches and httpx_mock raises, catching the regression.
    app = _create_social_app("ocs")
    token = SocialToken(token="real-access-token-value")

    httpx_mock.add_response(
        url=OcsOAuth2Adapter.profile_url,
        match_headers={"Authorization": "Bearer real-access-token-value"},
        json={"sub": "ocs-user-123", "email": "a@b.com"},
    )

    request = rf.get("/")
    adapter = OcsOAuth2Adapter(request)

    sociallogin = adapter.complete_login(request, app, token)

    assert sociallogin.account.uid == "ocs-user-123"
