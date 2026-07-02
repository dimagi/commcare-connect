import datetime

import httpx
from allauth.socialaccount.models import SocialAccount, SocialToken
from django.db import transaction
from django.utils import timezone


class TokenRefreshError(Exception):
    """Raised when an OAuth2 access token could not be refreshed."""


class SocialTokenMissingError(TokenRefreshError):
    """Raised when the user has no connected account/token for the provider."""


def refresh_access_token(user, provider, token_url, *, force=False):
    token = _get_social_token(user, provider)
    if not force and _is_valid(token):
        return token

    # Refresh tokens are single-use and rotate, so lock the row to serialise concurrent
    # refreshes (e.g. the bulk-assign loop) and re-check validity once we hold the lock.
    with transaction.atomic():
        token = SocialToken.objects.select_related("app").select_for_update(of=("self",)).get(pk=token.pk)
        if not force and _is_valid(token):
            return token
        _refresh(token, token_url)
    return token


def _get_social_token(user, provider):
    account = SocialAccount.objects.filter(user=user, provider=provider).first()
    if account is None:
        raise SocialTokenMissingError(f"User has no connected {provider} account")
    token = SocialToken.objects.select_related("app").filter(account=account).first()
    if token is None:
        raise SocialTokenMissingError(f"User has no stored {provider} token")
    return token


def _is_valid(token):
    return token.expires_at is not None and token.expires_at > timezone.now()


def _refresh(token, token_url):
    try:
        response = httpx.post(
            token_url,
            data={
                "grant_type": "refresh_token",
                "client_id": token.app.client_id,
                "client_secret": token.app.secret,
                "refresh_token": token.token_secret,
            },
        )
    except httpx.RequestError as e:
        raise TokenRefreshError(f"Failed to refresh token: {e}") from e
    if response.status_code != 200:
        raise TokenRefreshError(f"Failed to refresh token: {response.text}")

    data = response.json()
    access_token = data.get("access_token")
    if not access_token:
        raise TokenRefreshError(f"Token refresh response missing access_token: {response.text}")

    token.token = access_token
    if data.get("refresh_token"):
        token.token_secret = data["refresh_token"]

    if data.get("expires_in") is not None:
        token.expires_at = timezone.now() + datetime.timedelta(seconds=int(data["expires_in"]))
    token.save()
