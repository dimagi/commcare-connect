import time
from unittest import mock
from urllib.parse import urlencode

import pytest
from django.conf import settings
from django.urls import reverse
from oauth2_provider.models import Application
from rest_framework.test import APIClient

from commcare_connect.commcarehq.tests.factories import OauthApplicationFactory
from commcare_connect.opportunity.models import AssignedTask, TaskTypeModeChoices
from commcare_connect.opportunity.tests.factories import AssignedTaskFactory
from commcare_connect.users.models import User
from commcare_connect.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

TOKEN_URL = reverse("oauth2_provider:token")
USERINFO_URL = reverse("oauth2_provider:user-info")
TASK_CALLBACK_URL = reverse("api:task_completed")
EXPORT_URL = reverse("data_export:opp_org_program_list")


def _issue_client_credentials_token(*, application, plaintext_secret, scope):
    """Real client_credentials round trip against /o/token/."""
    token_response = APIClient().post(
        TOKEN_URL,
        data=urlencode(
            {
                "grant_type": "client_credentials",
                "client_id": application.client_id,
                "client_secret": plaintext_secret,
                "scope": scope,
            }
        ),
        content_type="application/x-www-form-urlencoded",
    )
    assert token_response.status_code == 200, token_response.content
    return token_response.json()


def _issue_password_grant_token(*, application, plaintext_secret, user, plaintext_password, scope):
    """Real password-grant round trip against /o/token/ — used to obtain a token that,
    unlike client_credentials, is tied to a real `user` (needed for IsAuthenticated views)."""
    user.set_password(plaintext_password)
    user.save()

    token_response = APIClient().post(
        TOKEN_URL,
        data=urlencode(
            {
                "grant_type": "password",
                "username": user.username,
                "password": plaintext_password,
                "client_id": application.client_id,
                "client_secret": plaintext_secret,
                "scope": scope,
            }
        ),
        content_type="application/x-www-form-urlencoded",
    )
    assert token_response.status_code == 200, token_response.content
    return token_response.json()


class TestClientCredentialsGrant:
    """Mirrors how CommCare HQ authenticates against Connect's own OAuth2 provider."""

    def test_issued_token_authenticates_protected_view(self):
        plaintext_secret = "test-client-secret-value"
        application = OauthApplicationFactory(
            client_type=Application.CLIENT_CONFIDENTIAL,
            authorization_grant_type=Application.GRANT_CLIENT_CREDENTIALS,
            client_secret=plaintext_secret,
        )
        token_data = _issue_client_credentials_token(
            application=application, plaintext_secret=plaintext_secret, scope="read write"
        )
        assert token_data["scope"] == "read write"

        task = AssignedTaskFactory(task_type__mode=TaskTypeModeChoices.OCS)
        client = APIClient()
        client.credentials(Authorization=f"Bearer {token_data['access_token']}")
        with mock.patch.object(AssignedTask, "mark_completed") as mark_completed:
            callback_response = client.post(
                TASK_CALLBACK_URL, data={"connectTaskId": str(task.assigned_task_id)}, format="json"
            )
        assert callback_response.status_code == 200, callback_response.content
        mark_completed.assert_called_once()

    def test_wrong_client_secret_is_rejected(self):
        application = OauthApplicationFactory(
            client_type=Application.CLIENT_CONFIDENTIAL,
            authorization_grant_type=Application.GRANT_CLIENT_CREDENTIALS,
            client_secret="correct-secret",
        )

        token_response = APIClient().post(
            TOKEN_URL,
            data=urlencode(
                {
                    "grant_type": "client_credentials",
                    "client_id": application.client_id,
                    "client_secret": "wrong-secret",
                }
            ),
            content_type="application/x-www-form-urlencoded",
        )
        assert token_response.status_code == 401, token_response.content


class TestResourceServerIntrospection:
    """
    Mirrors how Connect validates tokens it did not issue itself: ConnectID-issued
    tokens (e.g. mobile app logins), verified via RESOURCE_SERVER_INTROSPECTION_URL.
    """

    def test_authenticates_request_via_connectid_introspection(self):
        introspection_response = mock.Mock(status_code=200)
        introspection_response.json.return_value = {
            "active": True,
            "username": "mobile-worker-1",
            "scope": "read write",
            "exp": int(time.time()) + 3600,
        }
        task = AssignedTaskFactory(task_type__mode=TaskTypeModeChoices.OCS)
        client = APIClient()
        client.credentials(Authorization="Bearer connectid-issued-token")

        with (
            mock.patch("oauth2_provider.oauth2_validators.requests.post", return_value=introspection_response) as post,
            mock.patch.object(AssignedTask, "mark_completed") as mark_completed,
        ):
            response = client.post(
                TASK_CALLBACK_URL, data={"connectTaskId": str(task.assigned_task_id)}, format="json"
            )

        assert response.status_code == 200, response.content
        mark_completed.assert_called_once()
        # Verify Connect actually hit *our* configured ConnectID introspection endpoint,
        # not just that some introspection call happened.
        assert post.call_args.args[0] == settings.OAUTH2_PROVIDER["RESOURCE_SERVER_INTROSPECTION_URL"]
        assert User.objects.filter(username="mobile-worker-1").exists()

    @pytest.mark.parametrize(
        "introspection_payload",
        [
            pytest.param({"active": False}, id="inactive"),
            pytest.param(
                {"active": True, "username": "mobile-worker-1", "exp": int(time.time()) - 3600},
                id="active_but_expired",
            ),
        ],
    )
    def test_rejects_invalid_introspection_response(self, introspection_payload):
        introspection_response = mock.Mock(status_code=200)
        introspection_response.json.return_value = introspection_payload
        client = APIClient()
        client.credentials(Authorization="Bearer some-connectid-token")

        with mock.patch("oauth2_provider.oauth2_validators.requests.post", return_value=introspection_response):
            response = client.post(TASK_CALLBACK_URL, data={"connectTaskId": "does-not-matter"}, format="json")

        assert response.status_code == 401, response.content


class TestUserInfoEndpoint:
    """Exercises CustomOAuth2Validator.get_userinfo_claims end-to-end via /o/userinfo/"""

    def test_get_userinfo_claims_returns_custom_claims(self):
        plaintext_secret = "userinfo-test-secret"
        application = OauthApplicationFactory(
            client_type=Application.CLIENT_CONFIDENTIAL,
            authorization_grant_type=Application.GRANT_PASSWORD,
            client_secret=plaintext_secret,
        )
        user = UserFactory(name="Jane Doe")
        token_data = _issue_password_grant_token(
            application=application,
            plaintext_secret=plaintext_secret,
            user=user,
            plaintext_password="known-test-password",
            scope="openid read",
        )
        assert "openid" in token_data["scope"].split()

        client = APIClient()
        client.credentials(Authorization=f"Bearer {token_data['access_token']}")
        response = client.get(USERINFO_URL)

        assert response.status_code == 200, response.content
        claims = response.json()
        assert claims["name"] == user.name
        assert claims["email"] == user.email
        assert claims["username"] == user.username


class TestScopeEnforcement:
    """Mirrors the export-scope check used by data_export (TokenHasScope + IsAuthenticated,
    a different permission class than TokenHasReadWriteScope covered above)."""

    def test_token_without_export_scope_is_denied(self):
        plaintext_secret = "no-export-scope-secret"
        application = OauthApplicationFactory(
            client_type=Application.CLIENT_CONFIDENTIAL,
            authorization_grant_type=Application.GRANT_PASSWORD,
            client_secret=plaintext_secret,
        )
        user = UserFactory()
        token_data = _issue_password_grant_token(
            application=application,
            plaintext_secret=plaintext_secret,
            user=user,
            plaintext_password="known-test-password",
            scope="read write",
        )
        assert "export" not in token_data["scope"].split()

        client = APIClient()
        client.credentials(Authorization=f"Bearer {token_data['access_token']}")
        response = client.get(EXPORT_URL)
        assert response.status_code == 403, response.content

    def test_token_with_export_scope_from_real_grant_is_allowed(self):
        plaintext_secret = "export-scope-secret"
        application = OauthApplicationFactory(
            client_type=Application.CLIENT_CONFIDENTIAL,
            authorization_grant_type=Application.GRANT_PASSWORD,
            client_secret=plaintext_secret,
        )
        user = UserFactory()
        token_data = _issue_password_grant_token(
            application=application,
            plaintext_secret=plaintext_secret,
            user=user,
            plaintext_password="known-test-password",
            scope="export",
        )

        client = APIClient()
        client.credentials(Authorization=f"Bearer {token_data['access_token']}")
        response = client.get(EXPORT_URL)
        assert response.status_code == 200, response.content
