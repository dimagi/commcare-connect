import datetime
import urllib.parse

import pytest
from allauth.socialaccount.models import SocialAccount, SocialApp, SocialToken
from django.utils import timezone

from commcare_connect.utils.oauth_tokens import (
    DEFAULT_ACCESS_TOKEN_TTL,
    SocialTokenMissingError,
    TokenRefreshError,
    refresh_access_token,
)

TOKEN_URL = "https://ocs.example.test/o/token/"


def _make_token(user, *, provider="ocs", access="old-access", refresh="old-refresh", expires_at, client_id=None):
    app = SocialApp.objects.create(
        provider=provider,
        name=provider,
        client_id=client_id or f"{provider}-client-id",
        secret=f"{provider}-secret",
    )
    account = SocialAccount.objects.create(user=user, provider=provider, uid=f"uid-{provider}")
    return SocialToken.objects.create(
        app=app,
        account=account,
        token=access,
        token_secret=refresh,
        expires_at=expires_at,
    )


def _parse_body(request):
    return {k: v[0] for k, v in urllib.parse.parse_qs(request.read().decode()).items()}


@pytest.mark.django_db
class TestRefreshAccessToken:
    def test_returns_valid_token_without_refreshing(self, user, httpx_mock):
        future = timezone.now() + datetime.timedelta(minutes=30)
        _make_token(user, expires_at=future)

        result = refresh_access_token(user, provider="ocs", token_url=TOKEN_URL)

        assert result.token == "old-access"
        assert httpx_mock.get_requests() == []  # no network call when still valid

    def test_refreshes_expired_token(self, user, httpx_mock):
        past = timezone.now() - datetime.timedelta(minutes=5)
        _make_token(user, expires_at=past)
        httpx_mock.add_response(
            url=TOKEN_URL,
            method="POST",
            json={"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 3600},
        )

        result = refresh_access_token(user, provider="ocs", token_url=TOKEN_URL)

        assert result.token == "new-access"
        assert result.token_secret == "new-refresh"

    def test_force_refreshes_even_when_valid(self, user, httpx_mock):
        future = timezone.now() + datetime.timedelta(minutes=30)
        _make_token(user, expires_at=future)
        httpx_mock.add_response(url=TOKEN_URL, method="POST", json={"access_token": "forced", "expires_in": 3600})

        result = refresh_access_token(user, provider="ocs", token_url=TOKEN_URL, force=True)

        assert result.token == "forced"

    def test_expiry_uses_response_expires_in_not_hardcoded(self, user, httpx_mock):
        past = timezone.now() - datetime.timedelta(minutes=5)
        _make_token(user, expires_at=past)
        httpx_mock.add_response(url=TOKEN_URL, method="POST", json={"access_token": "new", "expires_in": 3600})

        result = refresh_access_token(user, provider="ocs", token_url=TOKEN_URL)

        expected = timezone.now() + datetime.timedelta(seconds=3600)
        assert abs((result.expires_at - expected).total_seconds()) < 10
        # regression guard against the old hardcoded 900s
        assert result.expires_at > timezone.now() + datetime.timedelta(seconds=1000)

    def test_expiry_falls_back_to_default_when_expires_in_absent(self, user, httpx_mock):
        # A 200 refresh with no expires_in must still advance expires_at, otherwise
        # every subsequent call would re-refresh and burn a single-use refresh token.
        past = timezone.now() - datetime.timedelta(minutes=5)
        _make_token(user, expires_at=past)
        httpx_mock.add_response(url=TOKEN_URL, method="POST", json={"access_token": "new"})

        result = refresh_access_token(user, provider="ocs", token_url=TOKEN_URL)

        assert result.expires_at > timezone.now() + datetime.timedelta(seconds=60)
        expected = timezone.now() + DEFAULT_ACCESS_TOKEN_TTL
        assert abs((result.expires_at - expected).total_seconds()) < 10

    def test_valid_after_refresh_without_expires_in_does_not_refetch(self, user, httpx_mock):
        past = timezone.now() - datetime.timedelta(minutes=5)
        _make_token(user, expires_at=past)
        httpx_mock.add_response(url=TOKEN_URL, method="POST", json={"access_token": "new"})

        refresh_access_token(user, provider="ocs", token_url=TOKEN_URL)
        # Second call should treat the refreshed token as valid — no further network call.
        result = refresh_access_token(user, provider="ocs", token_url=TOKEN_URL)

        assert result.token == "new"
        assert len(httpx_mock.get_requests()) == 1

    def test_keeps_existing_refresh_token_when_response_omits_it(self, user, httpx_mock):
        past = timezone.now() - datetime.timedelta(minutes=5)
        _make_token(user, refresh="keep-me", expires_at=past)
        httpx_mock.add_response(url=TOKEN_URL, method="POST", json={"access_token": "new", "expires_in": 3600})

        result = refresh_access_token(user, provider="ocs", token_url=TOKEN_URL)

        assert result.token == "new"
        assert result.token_secret == "keep-me"

    def test_refreshes_the_correct_provider_token(self, user, httpx_mock):
        past = timezone.now() - datetime.timedelta(minutes=5)
        future = timezone.now() + datetime.timedelta(minutes=30)
        hq_token = _make_token(
            user, provider="commcarehq", access="hq-access", refresh="hq-refresh", expires_at=future
        )
        _make_token(user, provider="ocs", access="ocs-access", refresh="ocs-refresh", expires_at=past)
        httpx_mock.add_response(url=TOKEN_URL, method="POST", json={"access_token": "ocs-new", "expires_in": 3600})

        result = refresh_access_token(user, provider="ocs", token_url=TOKEN_URL)

        assert result.token == "ocs-new"
        body = _parse_body(httpx_mock.get_requests()[0])
        assert body["refresh_token"] == "ocs-refresh"  # used the OCS token, not HQ's
        assert body["client_id"] == "ocs-client-id"
        hq_token.refresh_from_db()
        assert hq_token.token == "hq-access"  # HQ token untouched

    def test_sends_refresh_token_grant_payload(self, user, httpx_mock):
        past = timezone.now() - datetime.timedelta(minutes=5)
        _make_token(user, expires_at=past)
        httpx_mock.add_response(url=TOKEN_URL, method="POST", json={"access_token": "new", "expires_in": 3600})

        refresh_access_token(user, provider="ocs", token_url=TOKEN_URL)

        body = _parse_body(httpx_mock.get_requests()[0])
        assert body["grant_type"] == "refresh_token"
        assert body["refresh_token"] == "old-refresh"
        assert body["client_id"] == "ocs-client-id"
        assert body["client_secret"] == "ocs-secret"

    def test_raises_when_no_social_account(self, user):
        with pytest.raises(SocialTokenMissingError):
            refresh_access_token(user, provider="ocs", token_url=TOKEN_URL)

    def test_raises_when_account_has_no_token(self, user):
        SocialApp.objects.create(provider="ocs", name="ocs", client_id="c", secret="s")
        SocialAccount.objects.create(user=user, provider="ocs", uid="uid-ocs")

        with pytest.raises(SocialTokenMissingError):
            refresh_access_token(user, provider="ocs", token_url=TOKEN_URL)

    def test_raises_on_non_200_response(self, user, httpx_mock):
        past = timezone.now() - datetime.timedelta(minutes=5)
        _make_token(user, expires_at=past)
        httpx_mock.add_response(url=TOKEN_URL, method="POST", status_code=400, text="invalid_grant")

        with pytest.raises(TokenRefreshError):
            refresh_access_token(user, provider="ocs", token_url=TOKEN_URL)

    def test_raises_when_200_response_missing_access_token(self, user, httpx_mock):
        past = timezone.now() - datetime.timedelta(minutes=5)
        _make_token(user, expires_at=past)
        httpx_mock.add_response(url=TOKEN_URL, method="POST", json={"expires_in": 3600})

        with pytest.raises(TokenRefreshError):
            refresh_access_token(user, provider="ocs", token_url=TOKEN_URL)

    def test_raises_token_refresh_error_on_network_error(self, user, httpx_mock):
        import httpx

        past = timezone.now() - datetime.timedelta(minutes=5)
        _make_token(user, expires_at=past)
        httpx_mock.add_exception(httpx.ConnectError("boom"), url=TOKEN_URL, method="POST")

        with pytest.raises(TokenRefreshError):
            refresh_access_token(user, provider="ocs", token_url=TOKEN_URL)
