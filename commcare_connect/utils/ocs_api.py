import httpx
from allauth.socialaccount.models import SocialAccount
from django.conf import settings

from commcare_connect.ocs_provider.provider import OcsProvider
from commcare_connect.utils.oauth_tokens import refresh_access_token


class OcsApiError(Exception):
    """Raised when an OCS API call fails."""


def user_has_connected_ocs(user) -> bool:
    return SocialAccount.objects.filter(user=user, provider=OcsProvider.id).exists()


def list_chatbots(user) -> list[tuple[str, str]]:
    """Return ``[(id, name), ...]`` for every OCS chatbot, following cursor pagination."""
    token = _valid_token(user)
    headers = {"Authorization": f"Bearer {token.token}"}
    url = f"{settings.OCS_BASE_URL}/api/v2/chatbots/"
    chatbots = []
    while url:
        try:
            response = httpx.get(url, headers=headers)
            response.raise_for_status()
        except httpx.HTTPStatusError:
            raise OcsApiError(f"Failed to list chatbots: {response.text}")
        except httpx.RequestError as e:
            raise OcsApiError(f"Failed to list chatbots: {e}")
        data = response.json()
        chatbots.extend((c["id"], c["name"]) for c in data.get("results", []))
        url = data.get("next")
    return chatbots


def trigger_bot(
    user,
    *,
    identifier,
    experiment,
    participant_data=None,
    start_new_session=None,
    message_text=None,
    prompt_text=None,
    session_data=None,
) -> dict:
    """Trigger an OCS bot for ``identifier`` on ``experiment``; return the parsed response."""
    token = _valid_token(user)
    payload = {"identifier": identifier, "experiment": experiment, "platform": "commcare_connect"}
    optionals = {
        "participant_data": participant_data,
        "start_new_session": start_new_session,
        "message_text": message_text,
        "prompt_text": prompt_text,
        "session_data": session_data,
    }
    payload.update({k: v for k, v in optionals.items() if v is not None})

    try:
        response = httpx.post(
            f"{settings.OCS_BASE_URL}/api/trigger_bot",
            json=payload,
            headers={"Authorization": f"Bearer {token.token}"},
        )
        response.raise_for_status()
    except httpx.HTTPStatusError:
        raise OcsApiError(f"Failed to trigger bot: {response.text}")
    except httpx.RequestError as e:
        raise OcsApiError(f"Failed to trigger bot: {e}")
    return response.json()


def _valid_token(user):
    return refresh_access_token(user, provider=OcsProvider.id, token_url=f"{settings.OCS_BASE_URL}/o/token/")
