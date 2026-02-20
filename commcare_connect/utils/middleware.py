from django.contrib import messages
from django.http import HttpResponseRedirect
from django.utils.safestring import mark_safe
from pghistory.middleware import HistoryMiddleware
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


class CustomPGHistoryMiddleware(HistoryMiddleware):
    def get_context(self, request):
        context = super().get_context(request)
        if request.user.is_authenticated:
            self._add_user_details_to_context(request, context)
        return context

    @staticmethod
    def _add_user_details_to_context(request, context):
        """
        Store user email & username on context to avoid losing them in case the user is deleted, which is critical
        for an audit record to be useful.
        This additionally helps avoid a lookup when displaying this audit record.
        """

        # Even though this would never be false, this ensures that here we use the same user as used by pghistory.
        # Using an assertion to fail hard since this would be critical, hence, to avoid any faulty audit records.
        # Note: "user" in context is the ID of the authenticated user & is added by pghistory
        assert request.user.pk == context["user"]

        # add username to context if user is authenticated
        context["username"] = request.user.username
        context["user_email"] = request.user.email
