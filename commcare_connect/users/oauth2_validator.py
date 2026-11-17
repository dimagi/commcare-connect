import logging

from oauth2_provider.oauth2_validators import OAuth2Validator
from oauthlib.oauth2.rfc6749.errors import InvalidScopeError

logger = logging.getLogger(__name__)

OPENID_SCOPE = "openid"


def _client_supports_oidc(client):
    """An application can only sign ID tokens if it has a signing algorithm configured."""
    return bool(getattr(client, "algorithm", ""))


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
        """
        if OPENID_SCOPE in scopes and getattr(request, "response_type", None) and not _client_supports_oidc(client):
            logger.warning(
                "Rejected openid scope for OAuth application without an ID token signing algorithm",
                extra={"client_id": client_id, "scopes": list(scopes)},
            )
            return False
        return super().validate_scopes(client_id, scopes, client, request, *args, **kwargs)

    def finalize_id_token(self, id_token, token, token_handler, request):
        # Backstop for every flow that actually signs an ID token, including codes issued
        # before validate_scopes started rejecting openid for these apps.
        if not _client_supports_oidc(request.client):
            logger.warning(
                "ID token requested for OAuth application without an ID token signing algorithm",
                extra={"client_id": request.client_id, "scopes": list(request.scopes or [])},
            )
            raise InvalidScopeError(
                description="This application is not configured for OpenID Connect (no signing algorithm set).",
                request=request,
            )
        return super().finalize_id_token(id_token, token, token_handler, request)
