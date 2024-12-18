from django.conf import settings
from django.contrib import messages
from django.core.exceptions import MiddlewareNotUsed
from django.http import HttpResponseRedirect
from django.utils.safestring import mark_safe
from rest_framework.settings import api_settings

from commcare_connect.utils.commcarehq_api import CommCareTokenException

API_KEY_ERROR = """
    Unable to retrieve applications from CommCare HQ.<br>
    Please re-login using CommCare HQ or add a <a href="{url}">CommCare API Key</a>.
"""


class CustomErrorHandlingMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, *args, **kwargs):
        return self.get_response(*args, **kwargs)

    def process_exception(self, request, exception):
        if isinstance(exception, CommCareTokenException):
            api_url = "#"  # TODO: make this a real URL
            messages.error(request, mark_safe(API_KEY_ERROR.format(url=api_url)))
            return HttpResponseRedirect(request.headers["referer"])


class CurrentVersionMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.include_version_headers = False
        response = self.get_response(request)
        if request.include_version_headers:
            response.headers["X-API-Current-Version"] = api_settings.DEFAULT_VERSION

        return response

    def process_view(self, request, view_func, view_args, view_kwargs):
        if hasattr(view_func, "cls") and view_func.cls.versioning_class is not None:
            request.include_version_headers = True


class SentryContextMiddleware:
    """Add details to Sentry context.
    Should be placed after '"commcare_connect.users.middleware.OrganizationMiddleware",'
    """

    def __init__(self, get_response):
        self.get_response = get_response
        try:
            from sentry_sdk import Scope  # noqa: F401
        except ImportError:
            raise MiddlewareNotUsed

        if not getattr(settings, "SENTRY_DSN", None):
            raise MiddlewareNotUsed

    def process_view(self, request, view_func, view_args, view_kwargs):
        from sentry_sdk import configure_scope

        with configure_scope() as scope:
            if getattr(request, "user", None):
                scope.set_user("username", request.user.username)

            if getattr(request, "org", None):
                scope.set_tag("org", request.org)
