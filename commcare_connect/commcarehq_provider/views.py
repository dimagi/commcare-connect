import httpx
from allauth.socialaccount.providers.oauth2.client import OAuth2Error
from allauth.socialaccount.providers.oauth2.views import OAuth2Adapter, OAuth2CallbackView, OAuth2LoginView
from django.conf import settings

from .provider import CommcareHQProvider


class CommcareHQOAuth2Adapter(OAuth2Adapter):
    provider_id = CommcareHQProvider.id
    access_token_url = f"{settings.COMMCARE_HQ_URL}/oauth/token/"
    authorize_url = f"{settings.COMMCARE_HQ_URL}/oauth/authorize/"
    profile_url = f"{settings.COMMCARE_HQ_URL}/api/v0.5/identity/"
    supports_state = False
    redirect_uri_protocol = "https"

    def complete_login(self, request, app, token, **kwargs):
        response = httpx.get(self.profile_url, headers={"Authorization": f"Bearer {token}"})
        if response.status_code != 200:
            raise OAuth2Error("Failed to fetch profile data from CommCare HQ.")
        extra_data = response.json()
        return self.get_provider().sociallogin_from_response(request, extra_data)


oauth2_login = OAuth2LoginView.adapter_view(CommcareHQOAuth2Adapter)
oauth2_callback = OAuth2CallbackView.adapter_view(CommcareHQOAuth2Adapter)
