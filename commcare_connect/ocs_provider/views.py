import httpx
from allauth.socialaccount.providers.oauth2.views import OAuth2Adapter, OAuth2CallbackView, OAuth2LoginView
from django.conf import settings

from .provider import OcsProvider


class OcsOAuth2Adapter(OAuth2Adapter):
    provider_id = OcsProvider.id
    access_token_url = f"{settings.OCS_BASE_URL}/o/token/"
    authorize_url = f"{settings.OCS_BASE_URL}/o/authorize/"
    profile_url = f"{settings.OCS_BASE_URL}/o/userinfo/"
    redirect_uri_protocol = "https"

    def complete_login(self, request, app, token, **kwargs):
        response = httpx.get(self.profile_url, headers={"Authorization": f"Bearer {token}"})
        extra_data = response.json()
        return self.get_provider().sociallogin_from_response(request, extra_data)


oauth2_login = OAuth2LoginView.adapter_view(OcsOAuth2Adapter)
oauth2_callback = OAuth2CallbackView.adapter_view(OcsOAuth2Adapter)
