"""
Labs Authentication Backend

Minimal backend for labs environment.
Actual authentication is handled by middleware reading from session.
"""


class LabsOAuthBackend:
    """Session-based authentication backend for labs environment.

    This is a minimal backend that satisfies Django's authentication system.
    The actual authentication logic is in LabsAuthenticationMiddleware which
    populates request.user from session data.
    """

    def authenticate(self, request, **kwargs):
        """Not used - middleware handles authentication."""
        return None

    def get_user(self, user_id):
        """Not used - we don't load users from database."""
        return None
