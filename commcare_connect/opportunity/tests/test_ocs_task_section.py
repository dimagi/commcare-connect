from unittest.mock import patch

import pytest
from allauth.socialaccount.models import SocialAccount

from commcare_connect.opportunity.views import get_ocs_task_section_context
from commcare_connect.utils.oauth_tokens import SocialTokenMissingError, TokenRefreshError
from commcare_connect.utils.ocs_api import OcsApiError


@pytest.mark.django_db
def test_context_disconnected_returns_next_url(rf, user):
    request = rf.get(
        "/a/org/opportunity/task_types/ocs_section/", HTTP_HX_CURRENT_URL="/a/org/opportunity/task_types/"
    )
    request.user = user

    context = get_ocs_task_section_context(request)

    assert context["ocs_connected"] is False
    assert context["ocs_next_url"] == "/a/org/opportunity/task_types/"


def _assert_lists_chatbots(context):
    assert context["chatbots"] == [("bot-1", "Bot One"), ("bot-2", "Bot Two")]
    assert "ocs_error" not in context


def _assert_api_error(context):
    assert context["ocs_error"] is True


@pytest.mark.django_db
@pytest.mark.parametrize(
    "list_chatbots_kwargs, assert_context",
    [
        ({"return_value": [("bot-1", "Bot One"), ("bot-2", "Bot Two")]}, _assert_lists_chatbots),
        ({"side_effect": OcsApiError("boom")}, _assert_api_error),
    ],
    ids=["lists_chatbots", "api_error"],
)
def test_context_connected(rf, user, list_chatbots_kwargs, assert_context):
    SocialAccount.objects.create(user=user, provider="ocs", uid="uid-ocs")
    request = rf.get("/a/org/opportunity/task_types/ocs_section/")
    request.user = user

    with patch("commcare_connect.opportunity.views.list_chatbots", **list_chatbots_kwargs):
        context = get_ocs_task_section_context(request)

    assert context["ocs_connected"] is True
    assert_context(context)


@pytest.mark.django_db
@pytest.mark.parametrize("error", [TokenRefreshError("revoked"), SocialTokenMissingError("gone")])
def test_context_token_error_prompts_reconnect(rf, user, error):
    # The account row exists, but the token is missing or its refresh token was
    # revoked/expired. Retrying can never succeed, so the user must reconnect.
    SocialAccount.objects.create(user=user, provider="ocs", uid="uid-ocs")
    request = rf.get(
        "/a/org/opportunity/task_types/ocs_section/", HTTP_HX_CURRENT_URL="/a/org/opportunity/task_types/"
    )
    request.user = user

    with patch("commcare_connect.opportunity.views.list_chatbots", side_effect=error):
        context = get_ocs_task_section_context(request)

    assert context["ocs_connected"] is False
    assert context["ocs_next_url"] == "/a/org/opportunity/task_types/"
    assert "ocs_error" not in context
