import datetime

from django.utils.timezone import now


def add_token_credentials(api_client, user, token="token", scope="read write", application=None):
    """Authenticate api_client as user with an OAuth bearer token, keeping any credentials already set."""
    access_token, _ = user.oauth2_provider_accesstoken.get_or_create(
        token=token,
        scope=scope,
        defaults={"expires": now() + datetime.timedelta(hours=1)},
        application=application,
    )
    credentials = getattr(api_client, "_credentials", {})
    api_client.credentials(**{**credentials, "Authorization": f"Bearer {access_token}"})
