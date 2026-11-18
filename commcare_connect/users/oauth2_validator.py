import logging

from oauth2_provider.models import AbstractApplication
from oauth2_provider.oauth2_validators import OAuth2Validator
from oauth2_provider.settings import oauth2_settings
from oauthlib.oauth2.rfc6749.errors import InvalidScopeError

logger = logging.getLogger(__name__)

OPENID_SCOPE = "openid"


class OIDCNotConfiguredError(InvalidScopeError):
    """invalid_scope raised from the token endpoint with proper JSON error-response headers.

    Errors raised after token request validation (e.g. from ``finalize_id_token``) are turned
    into responses by django-oauth-toolkit using only ``exc.headers``, which oauthlib leaves
    empty for 400s, so the JSON body would otherwise be served as ``text/html``.
    """

    description = "This application is not configured for OpenID Connect (no ID token signing key available)."

    @property
    def headers(self):
        return {
            **super().headers,
            "Content-Type": "application/json",
            "Cache-Control": "no-store",
            "Pragma": "no-cache",
        }


def _client_supports_oidc(client):
    """An application can only sign ID tokens if it has a signing algorithm and a key for it.

    RS256 signs with the server-wide ``OIDC_RSA_PRIVATE_KEY``, which is optional in dev.
    """
    algorithm = getattr(client, "algorithm", "")
    if algorithm == AbstractApplication.RS256_ALGORITHM:
        return bool(oauth2_settings.OIDC_RSA_PRIVATE_KEY)
    return bool(algorithm)


class CustomOAuth2Validator(OAuth2Validator):
    def get_userinfo_claims(self, request):
        claims = super().get_userinfo_claims(request)
        claims["name"] = request.user.name
        claims["email"] = request.user.email
        claims["username"] = request.user.username
        return claims

    def validate_scopes(self, client_id, scopes, client, request, *args, **kwargs):
        """Refuse ``openid`` at the authorize endpoint for apps that cannot sign ID tokens.

        The check is deliberately limited to authorization requests (``response_type`` set):

        - Only the authorization-code/implicit/hybrid OIDC flows issue ID tokens; the password
          and client_credentials grants accept ``openid`` (e.g. for /o/userinfo/) without
          signing anything, so rejecting it there would break working clients.
        - oauthlib does not call ``validate_scopes`` during the authorization-code exchange at
          /o/token/ (scopes come from the stored grant), so that path is guarded by
          ``finalize_id_token`` below instead.

        Returning False makes oauthlib raise ``invalid_scope``, which the authorize endpoint
        reports by redirecting to the client's redirect URI with the error in the query string
        (RFC 6749 section 4.1.2.1). There is no JSON body here, so the custom headers on
        ``OIDCNotConfiguredError`` (needed only for the token endpoint) do not apply.
        """
        if OPENID_SCOPE in scopes and getattr(request, "response_type", None) and not _client_supports_oidc(client):
            logger.warning(
                "Rejected openid scope for OAuth application that cannot sign ID tokens",
                extra={"client_id": client_id, "scopes": list(scopes)},
            )
            return False
        return super().validate_scopes(client_id, scopes, client, request, *args, **kwargs)

    def finalize_id_token(self, id_token, token, token_handler, request):
        # Backstop for every flow that actually signs an ID token, including codes issued
        # before validate_scopes started rejecting openid for these apps.
        if not _client_supports_oidc(request.client):
            logger.warning(
                "ID token requested for OAuth application that cannot sign ID tokens",
                extra={"client_id": request.client_id, "scopes": list(request.scopes or [])},
            )
            raise OIDCNotConfiguredError(request=request)
        return super().finalize_id_token(id_token, token, token_handler, request)
