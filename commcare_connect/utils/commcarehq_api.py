import datetime

import httpx
from allauth.socialaccount.models import SocialAccount, SocialApp, SocialToken
from asgiref.sync import async_to_sync
from django.conf import settings
from django.utils import timezone

from commcare_connect.opportunity.models import HQApiKey


class CommCareHQAPIException(Exception):
    pass


class CommCareTokenException(CommCareHQAPIException):
    pass


def refresh_access_token(user, force=False):
    social_app = SocialApp.objects.filter(provider="commcarehq").first()
    social_acc = SocialAccount.objects.filter(user=user).first()
    social_token = SocialToken.objects.filter(account=social_acc).first()

    if not all([social_app, social_acc, social_token]):
        raise CommCareTokenException("User does not have a valid token")

    if not force and social_token.expires_at > timezone.now():
        return social_token

    response = httpx.post(
        f"{settings.COMMCARE_HQ_URL}/oauth/token/",
        data={
            "grant_type": "refresh_token",
            "client_id": social_app.client_id,
            "client_secret": social_app.secret,
            "refresh_token": social_token.token_secret,
        },
    )
    if response.status_code != 200:
        raise CommCareHQAPIException(f"Failed to refresh token: {response.text}")

    data = response.json()
    if data.get("access_token", ""):
        social_token.token = data["access_token"]
        social_token.token_secret = data["refresh_token"]
        social_token.expires_at = timezone.now() + datetime.timedelta(seconds=900)
        social_token.save()

    return social_token


def get_domains_for_user(api_key):
    response = httpx.get(
        f"{api_key.hq_server.url}/api/v0.5/user_domains/?limit=100",
        headers={"Authorization": f"ApiKey {api_key.user.email}:{api_key.api_key}"},
    )
    if response.status_code != 200:
        raise CommCareHQAPIException(f"Failed to fetch domains: {response.text}")
    data = response.json()
    domains = [domain["domain_name"] for domain in data["objects"]]
    return domains


def get_applications_for_user_by_domain(api_key: HQApiKey, domain):
    user_email = api_key.user.email
    hq_server_url = api_key.hq_server.url
    api_key = api_key.api_key
    return _get_applications_for_domain(user_email, api_key, domain, hq_server_url)


@async_to_sync
async def _get_applications_for_domain(user_email, api_key, domain, hq_server_url):
    async with httpx.AsyncClient(
        timeout=300,
        headers={
            "Authorization": f"ApiKey {user_email}:{api_key}",
        },
        base_url=hq_server_url,
    ) as client:
        applications = await _get_commcare_app_json(client, domain)
    return applications


async def _get_commcare_app_json(client, domain):
    applications = []
    response = await client.get(f"/a/{domain}/api/v0.5/application/")
    if response.status_code != 200:
        raise CommCareHQAPIException(f"Failed to fetch applications: {response.text}")
    data = response.json()

    for application in data.get("objects", []):
        applications.append({"id": application.get("id"), "name": application.get("name")})
    return applications
