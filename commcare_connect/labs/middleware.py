"""
Labs Middleware

Session-based authentication and URL whitelisting for labs environment.
"""

import logging

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.utils import timezone

from .models import LabsUser

logger = logging.getLogger(__name__)


class LabsAuthenticationMiddleware:
    """Populate request.user from session OAuth data (labs environment only).

    This middleware replaces Django's standard AuthenticationMiddleware in labs.
    It reads OAuth token and user profile from session and creates a transient
    LabsUser object (never saved to database).
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        # Only run in labs environment
        if not getattr(settings, "IS_LABS_ENVIRONMENT", False):
            return self.get_response(request)

        # Check session for OAuth data
        labs_oauth = request.session.get("labs_oauth")

        if labs_oauth:
            # Check token expiration
            expires_at = labs_oauth.get("expires_at", 0)
            if timezone.now().timestamp() < expires_at:
                # Token valid, populate request.user with transient LabsUser
                try:
                    request.user = LabsUser(labs_oauth["user_profile"])
                except (KeyError, TypeError) as e:
                    # Invalid session data, clear it
                    logger.warning(f"Invalid session data structure: {str(e)}")
                    request.session.pop("labs_oauth", None)
                    request.user = AnonymousUser()
            else:
                # Token expired, clear session
                username = labs_oauth.get("user_profile", {}).get("username", "unknown")
                logger.info(f"OAuth token expired for user {username}")
                request.session.pop("labs_oauth", None)
                request.user = AnonymousUser()
        else:
            request.user = AnonymousUser()

        return self.get_response(request)


class LabsURLWhitelistMiddleware:
    """Redirect non-whitelisted URLs to prod, require auth for whitelisted.

    In labs environment:
    - Non-whitelisted URLs redirect to connect.dimagi.com
    - Whitelisted URLs require authentication (except login/callback)
    """

    WHITELISTED_PREFIXES = [
        "/audit/",
        "/tasks/",
        "/labs/",
        "/static/",
        "/media/",
        "/admin/",
    ]

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        # Only run in labs environment
        is_labs = getattr(settings, "IS_LABS_ENVIRONMENT", False)
        if not is_labs:
            logger.debug(f"Not labs environment, passing through. IS_LABS_ENVIRONMENT={is_labs}")
            return self.get_response(request)

        path = request.path
        logger.debug(f"Labs middleware checking path: {path}, authenticated: {request.user.is_authenticated}")

        # Special case: root path redirects to labs login
        if path == "/":
            logger.debug("Redirecting root path to labs login")
            return HttpResponseRedirect("/labs/login/")

        # Check if path is whitelisted
        is_whitelisted = any(path.startswith(prefix) for prefix in self.WHITELISTED_PREFIXES)

        if not is_whitelisted:
            # Redirect to production Connect
            prod_url = f"https://connect.dimagi.com{path}"
            if request.GET:
                prod_url += f"?{request.GET.urlencode()}"
            logger.debug(f"Redirecting non-whitelisted path {path} to production")
            return HttpResponseRedirect(prod_url)

        # Whitelisted path - require authentication (except login/oauth/logout)
        public_paths = ["/labs/login/", "/labs/initiate/", "/labs/callback/", "/labs/logout/"]
        if path not in public_paths:
            if not request.user.is_authenticated:
                # Redirect to labs login with next parameter
                login_url = f"/labs/login/?next={path}"
                logger.debug(f"Redirecting unauthenticated user to login from {path}")
                return HttpResponseRedirect(login_url)

        return self.get_response(request)
