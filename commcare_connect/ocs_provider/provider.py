from allauth.socialaccount.providers.base import ProviderAccount
from allauth.socialaccount.providers.oauth2.provider import OAuth2Provider


class OcsAccount(ProviderAccount):
    def to_str(self):
        return self.account.extra_data.get("name", super().to_str())


class OcsProvider(OAuth2Provider):
    id = "ocs"
    name = "Open Chat Studio"
    account_class = OcsAccount
    pkce_enabled_default = True

    def get_default_scope(self):
        return ["chatbots:read", "chatbots:interact"]

    def extract_uid(self, data):
        return str(data.get("sub") or data["id"])


provider_classes = [OcsProvider]
