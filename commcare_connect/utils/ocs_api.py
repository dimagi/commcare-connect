import httpx
from allauth.socialaccount.models import SocialAccount
from django.conf import settings

from commcare_connect.ocs_provider.provider import OcsProvider
from commcare_connect.users.models import User
from commcare_connect.utils.oauth_tokens import refresh_access_token

OCS_HTTP_TIMEOUT = 10  # seconds


class OcsApiError(Exception):
    """Raised when an OCS API call fails."""


def user_has_connected_ocs(user) -> bool:
    return SocialAccount.objects.filter(user=user, provider=OcsProvider.id).exists()


def list_chatbots(user) -> list[tuple[str, str]]:
    """Return ``[(id, name), ...]`` for every OCS chatbot, following cursor pagination."""
    token = _get_valid_token(user)
    headers = {"Authorization": f"Bearer {token.token}"}
    url = f"{settings.OCS_BASE_URL}/api/v2/chatbots/"
    chatbots = []
    while url:
        try:
            response = httpx.get(url, headers=headers, timeout=OCS_HTTP_TIMEOUT)
            response.raise_for_status()
        except httpx.HTTPStatusError:
            raise OcsApiError(f"Failed to list chatbots: {response.text}")
        except httpx.RequestError as e:
            raise OcsApiError(f"Failed to list chatbots: {e}")
        try:
            data = response.json()
            chatbots.extend((c["id"], c["name"]) for c in data["results"])
            url = data.get("next")
        except (ValueError, KeyError, TypeError) as e:
            raise OcsApiError(f"Unexpected chatbots response from OCS: {e}")
    return chatbots


def trigger_bot(
    user: User,
    *,
    identifier: str,
    experiment: str,
    start_new_session: bool = True,
    session_data: dict | None = None,
    participant_data: dict | None = None,
) -> dict:
    """Trigger an OCS bot for ``identifier`` on ``experiment``; return the parsed response."""
    token = _get_valid_token(user)

    # prompt_text is not shown in the conversation and is only for getting the bot
    # to
    prompt_text = """
    Initiate the conversation by starting with "Greetings! You've been assigned training ..."
    """

    payload = {"identifier": "8796856837", "experiment": experiment, "platform": "telegram"}
    optionals = {
        "start_new_session": start_new_session,
        "session_data": session_data,
        "prompt_text": prompt_text,
        "participant_data": participant_data,
    }
    payload.update({k: v for k, v in optionals.items() if v is not None})

    try:
        response = httpx.post(
            f"{settings.OCS_BASE_URL}/api/trigger_bot",
            json=payload,
            headers={"Authorization": f"Bearer {token.token}"},
            timeout=OCS_HTTP_TIMEOUT,
        )
        response.raise_for_status()
    except httpx.HTTPStatusError:
        raise OcsApiError(f"Failed to trigger bot: {response.text}")
    except httpx.RequestError as e:
        raise OcsApiError(f"Failed to trigger bot: {e}")
    return response.json()


def _get_valid_token(user):
    return refresh_access_token(user, provider=OcsProvider.id, token_url=f"{settings.OCS_BASE_URL}/o/token/")
