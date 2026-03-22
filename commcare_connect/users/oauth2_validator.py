from oauth2_provider.oauth2_validators import OAuth2Validator


class CustomOAuth2Validator(OAuth2Validator):
    def get_userinfo_claims(self, request):
        claims = super().get_userinfo_claims(request)
        claims["name"] = request.user.name
        claims["email"] = request.user.email
        claims["username"] = request.user.username
        return claims
