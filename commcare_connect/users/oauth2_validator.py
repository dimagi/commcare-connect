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
        # Reject the openid scope on authorization requests (which go on to issue ID tokens)
        # for applications that have no ID token signing algorithm, rather than issuing a
        # code that later fails at the token endpoint.
        if OPENID_SCOPE in scopes and getattr(request, "response_type", None) and not _client_supports_oidc(client):
            logger.warning(
                "Rejected openid scope for OAuth application without an ID token signing algorithm",
                extra={"client_id": client_id, "scopes": list(scopes)},
            )
            return False
        return super().validate_scopes(client_id, scopes, client, request, *args, **kwargs)

    def finalize_id_token(self, id_token, token, token_handler, request):
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
