from django.conf import settings
from django.urls import reverse

from commcare_connect.ocs_provider.views import OcsOAuth2Adapter


def test_adapter_urls_derive_from_ocs_base_url():
    # The adapter binds its URLs from settings.OCS_BASE_URL at import time.
    assert OcsOAuth2Adapter.access_token_url == f"{settings.OCS_BASE_URL}/o/token/"
    assert OcsOAuth2Adapter.authorize_url == f"{settings.OCS_BASE_URL}/o/authorize/"
    assert OcsOAuth2Adapter.profile_url == f"{settings.OCS_BASE_URL}/o/userinfo/"


def test_login_and_callback_urls_are_registered():
    assert reverse("ocs_login") == "/accounts/ocs/login/"
    assert reverse("ocs_callback") == "/accounts/ocs/login/callback/"
