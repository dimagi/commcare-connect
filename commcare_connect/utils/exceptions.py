import logging

from rest_framework.exceptions import PermissionDenied
from rest_framework.views import exception_handler as drf_exception_handler

logger = logging.getLogger(__name__)


def drf_permission_denied_handler(exc, context):
    def _sanitize_headers(headers):
        SENSITIVE_HEADER_SUBSTRINGS = ("authorization", "cookie", "token", "key", "secret")
        sanitized = {}
        for header, value in headers.items():
            header_lower = header.lower()
            if any(sub in header_lower for sub in SENSITIVE_HEADER_SUBSTRINGS):
                sanitized[header] = "[REDACTED]"
            else:
                sanitized[header] = value
        return sanitized

    request = context.get("request")

    if isinstance(exc, (PermissionDenied)) and request is not None:
        user = getattr(request, "user", None)
        user_id = getattr(user, "id", None)
        auth_method = (
            request.successful_authenticator.__class__.__name__
            if hasattr(request, "successful_authenticator") and request.successful_authenticator
            else "None"
        )
        message = "User (ID: {user_id}) accessed {url} with headers {headers} using auth method {auth_method}".format(
            url=request.path,
            user_id=user_id,
            headers=_sanitize_headers(getattr(request, "headers", {})),
            auth_method=auth_method,
        )
        logger.warning(message)

    return drf_exception_handler(exc, context)
