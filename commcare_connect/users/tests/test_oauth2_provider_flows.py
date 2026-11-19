import base64
import hashlib
import json
import time
from datetime import timedelta
from unittest import mock
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from django.conf import settings
from django.urls import reverse
from django.utils import timezone
from jwcrypto import jwk, jwt
from oauth2_provider.models import Application, get_grant_model
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
        _assert_user_claims(response.json(), user)


def _assert_user_claims(claims, user):
    """The claims CustomOAuth2Validator releases for the openid scope."""
    assert claims["sub"] == str(user.pk)
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


REDIRECT_URI = "https://client.example.com/callback"
# PKCE is required by django-oauth-toolkit for the authorization code flow.
CODE_VERIFIER = "UUCp0rT9qN7t7V3x33QbgDwKKn5Ztbs2XoWZPnRb124WMecWoe05x1oLy4QHWOBW"
CODE_CHALLENGE = base64.urlsafe_b64encode(hashlib.sha256(CODE_VERIFIER.encode()).digest()).decode().rstrip("=")


def _oidc_application(*, algorithm, plaintext_secret):
    return OauthApplicationFactory(
        client_type=Application.CLIENT_CONFIDENTIAL,
        authorization_grant_type=Application.GRANT_AUTHORIZATION_CODE,
        client_secret=plaintext_secret,
        hash_client_secret=False,
        redirect_uris=REDIRECT_URI,
        algorithm=algorithm,
        skip_authorization=True,
    )


def _exchange_code(*, application, plaintext_secret, user, scope):
    """Exchange an authorization code issued with `scope` at /o/token/."""
    grant = get_grant_model().objects.create(
        application=application,
        user=user,
        code=f"code-{application.client_id}",
        expires=timezone.now() + timedelta(minutes=5),
        redirect_uri=REDIRECT_URI,
        scope=scope,
        nonce="n123",
        code_challenge=CODE_CHALLENGE,
        code_challenge_method="S256",
    )
    return _redeem_code(application=application, plaintext_secret=plaintext_secret, code=grant.code)


def _redeem_code(*, application, plaintext_secret, code):
    return APIClient().post(
        TOKEN_URL,
        data=urlencode(
            {
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": CODE_VERIFIER,
                "redirect_uri": REDIRECT_URI,
                "client_id": application.client_id,
                "client_secret": plaintext_secret,
            }
        ),
        content_type="application/x-www-form-urlencoded",
    )


def _authorize(client, application):
    """Request an openid authorization code at /o/authorize/ and return the redirect's query."""
    response = client.get(
        reverse("oauth2_provider:authorize"),
        {
            "response_type": "code",
            "client_id": application.client_id,
            "redirect_uri": REDIRECT_URI,
            "scope": "openid read",
            "code_challenge": CODE_CHALLENGE,
            "code_challenge_method": "S256",
        },
    )
    # Authorize-endpoint results, errors included, are reported by redirecting back to the client
    # with them in the query string (RFC 6749 section 4.1.2), not with a JSON body.
    assert response.status_code == 302, response.content
    location = urlsplit(response["Location"])
    assert f"{location.scheme}://{location.netloc}{location.path}" == REDIRECT_URI
    return parse_qs(location.query)


# Apps that cannot sign an ID token: no algorithm, or RS256 while OIDC_RSA_PRIVATE_KEY is unset.
UNSIGNABLE_ALGORITHMS = pytest.mark.parametrize("algorithm", [Application.NO_ALGORITHM, Application.RS256_ALGORITHM])


@pytest.mark.usefixtures("no_rsa_private_key")
class TestOpenIDConnectSigningAlgorithm:
    """Applications that cannot sign ID tokens must not 500 on openid requests."""

    @UNSIGNABLE_ALGORITHMS
    def test_openid_code_exchange_without_signing_key_returns_client_error(self, algorithm):
        plaintext_secret = "no-algorithm-secret"
        application = _oidc_application(algorithm=algorithm, plaintext_secret=plaintext_secret)

        response = _exchange_code(
            application=application, plaintext_secret=plaintext_secret, user=UserFactory(), scope="openid read"
        )

        assert response.status_code == 400, response.content
        assert response["Content-Type"] == "application/json"
        assert response.json()["error"] == "invalid_scope"

    @UNSIGNABLE_ALGORITHMS
    def test_openid_authorization_without_signing_key_is_rejected(self, client, algorithm):
        application = _oidc_application(algorithm=algorithm, plaintext_secret="authz-secret")
        client.force_login(UserFactory())

        query = _authorize(client, application)

        assert query["error"] == ["invalid_scope"]
        assert "code" not in query
        assert not get_grant_model().objects.filter(application=application).exists()


@pytest.fixture
def rsa_private_key(settings):
    pem = jwk.JWK.generate(kty="RSA", size=2048).export_to_pem(private_key=True, password=None).decode()
    _set_rsa_private_key(settings, pem)
    return pem


@pytest.fixture
def no_rsa_private_key(settings):
    # .env leaks into test settings, so a developer's local key must not decide these tests.
    _set_rsa_private_key(settings, "")


def _set_rsa_private_key(settings, pem):
    # Assign a new dict so setting_changed fires and django-oauth-toolkit reloads its settings.
    settings.OAUTH2_PROVIDER = {**settings.OAUTH2_PROVIDER, "OIDC_RSA_PRIVATE_KEY": pem}


def _issue_id_token(*, algorithm, plaintext_secret, user):
    application = _oidc_application(algorithm=algorithm, plaintext_secret=plaintext_secret)
    response = _exchange_code(
        application=application, plaintext_secret=plaintext_secret, user=user, scope="openid read"
    )
    assert response.status_code == 200, response.content
    return application, response.json()["id_token"]


class TestIDToken:
    def test_rs256_id_token_verifies_against_published_jwks(self, rsa_private_key):
        user = UserFactory(name="Jane Doe")
        application, id_token = _issue_id_token(
            algorithm=Application.RS256_ALGORITHM, plaintext_secret="rs256-secret", user=user
        )

        jwks = jwk.JWKSet.from_json(APIClient().get(reverse("oauth2_provider:jwks-info")).content)
        assert jwks["keys"]
        assert not any(key.has_private for key in jwks["keys"])
        token = jwt.JWT(jwt=id_token, key=jwks, algs=["RS256"])
        claims = json.loads(token.claims)

        assert token.token.jose_header["alg"] == "RS256"
        assert claims["aud"] == application.client_id
        _assert_user_claims(claims, user)

    @pytest.mark.usefixtures("no_rsa_private_key")
    def test_hs256_authorization_code_flow_issues_id_token_signed_with_client_secret(self, client):
        plaintext_secret = "hs256-secret"
        application = _oidc_application(algorithm=Application.HS256_ALGORITHM, plaintext_secret=plaintext_secret)
        user = UserFactory()
        client.force_login(user)

        query = _authorize(client, application)
        response = _redeem_code(application=application, plaintext_secret=plaintext_secret, code=query["code"][0])

        assert response.status_code == 200, response.content
        key = jwk.JWK.from_password(plaintext_secret)
        claims = json.loads(jwt.JWT(jwt=response.json()["id_token"], key=key, algs=["HS256"]).claims)
        _assert_user_claims(claims, user)


class TestDiscovery:
    def test_discovery_lists_signing_algorithms_and_claims(self, rsa_private_key):
        response = APIClient().get(reverse("oauth2_provider:oidc-connect-discovery-info"))

        assert response.status_code == 200, response.content
        data = response.json()
        assert "RS256" in data["id_token_signing_alg_values_supported"]
        assert {"sub", "name", "email", "username"} <= set(data["claims_supported"])
        assert data["jwks_uri"] == f"http://testserver{reverse('oauth2_provider:jwks-info')}"

    @pytest.mark.usefixtures("no_rsa_private_key")
    def test_jwks_is_empty_without_rsa_key(self):
        response = APIClient().get(reverse("oauth2_provider:jwks-info"))

        assert response.status_code == 200, response.content
        assert response.json() == {"keys": []}
