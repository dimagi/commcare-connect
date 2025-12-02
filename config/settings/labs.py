"""
Labs environment settings.

Session-based OAuth authentication with no user database storage.
"""

from .staging import *  # noqa

# Labs environment flags
IS_LABS_ENVIRONMENT = True
DEPLOY_ENVIRONMENT = "labs"

# OAuth configuration
LABS_OAUTH_SCOPES = ["export"]  # Expandable: ["export", "labs_data_storage"]

# Disable local registration
ACCOUNT_ALLOW_REGISTRATION = False

# Override login URL to labs OAuth
LOGIN_URL = "/labs/login/"

# Custom authentication (session-based, no DB)
AUTHENTICATION_BACKENDS = [
    "commcare_connect.labs.auth_backend.LabsOAuthBackend",
]

# Add labs app to installed apps
INSTALLED_APPS = INSTALLED_APPS + ["commcare_connect.labs"]  # noqa: F405

# Replace default AuthenticationMiddleware with labs version
# Add URL whitelist middleware
MIDDLEWARE = list(MIDDLEWARE)  # noqa: F405
_auth_idx = MIDDLEWARE.index("django.contrib.auth.middleware.AuthenticationMiddleware")
MIDDLEWARE[_auth_idx] = "commcare_connect.labs.middleware.LabsAuthenticationMiddleware"
MIDDLEWARE.insert(_auth_idx + 1, "commcare_connect.labs.middleware.LabsURLWhitelistMiddleware")
