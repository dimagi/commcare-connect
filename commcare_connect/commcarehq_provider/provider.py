from allauth.socialaccount.providers.base import ProviderAccount
from allauth.socialaccount.providers.oauth2.provider import OAuth2Provider

from .views import CommcareHQOAuth2Adapter


class CommcareHQAccount(ProviderAccount):
    def to_str(self):
        return self.account.extra_data.get("name", super().to_str())


class CommcareHQProvider(OAuth2Provider):
    id = "commcarehq"
    name = "commcarehq"
    account_class = CommcareHQAccount
    oauth2_adapter_class = CommcareHQOAuth2Adapter

    def get_default_scope(self):
        return ["access_apis"]

    def extract_uid(self, data):
        return str(data["id"])

    def extract_common_fields(self, data):
        first_name = data.get("first_name")
        last_name = data.get("last_name")
        return dict(
            email=data.get("email", ""),
            name=" ".join([first_name, last_name]),
            first_name=first_name,
            last_name=last_name,
        )


provider_classes = [CommcareHQProvider]
