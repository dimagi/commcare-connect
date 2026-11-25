from urllib.parse import urlencode

from django.urls import reverse

from commcare_connect.utils.tests.api import add_token_credentials


def add_export_credentials(api_client, user):
    add_token_credentials(api_client=api_client, user=user, token="export-token", scope="read write export")


def add_export_only_credentials(api_client, user):
    """Grants export scope but NOT write — used to assert write endpoints reject it."""
    add_token_credentials(api_client=api_client, user=user, token="export-only-token", scope="read export")


def export_url(url_name, opp_id, **params):
    url = reverse(f"data_export:{url_name}", kwargs={"opp_id": opp_id})
    return f"{url}?{urlencode(params)}" if params else url
