"""
Connect OAuth Provider

OAuth provider for authenticating with CommCare Connect production instance
to enable API-based data extraction.
"""

from allauth.socialaccount.providers.base import ProviderAccount
from allauth.socialaccount.providers.oauth2.provider import OAuth2Provider


class ConnectAccount(ProviderAccount):
    """Account class for Connect OAuth provider."""

    def to_str(self):
        return self.account.extra_data.get("username", super().to_str())


class ConnectProvider(OAuth2Provider):
    """OAuth provider for CommCare Connect production instance."""

    id = "connect"
    name = "CommCare Connect"
    account_class = ConnectAccount
    package = "commcare_connect.audit"

    def get_default_scope(self):
        """Default OAuth scope for data export."""
        return ["export"]

    def extract_uid(self, data):
        """Extract unique user ID from OAuth response."""
        return str(data["id"])

    def extract_common_fields(self, data):
        """Extract common user fields from OAuth response."""
        return dict(
            email=data.get("email", ""),
            username=data.get("username", ""),
            first_name=data.get("first_name", ""),
            last_name=data.get("last_name", ""),
        )


provider_classes = [ConnectProvider]
