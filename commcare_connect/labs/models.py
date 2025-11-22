"""
Labs User Model

Transient user object that mimics Django User interface.
Never saved to database - initialized from session data only.
"""

from typing import Any


def _get_empty_memberships():
    """Return empty queryset for memberships.

    Defined as function to avoid circular imports at module level.
    """
    from commcare_connect.organization.models import UserOrganizationMembership

    return UserOrganizationMembership.objects.none()


class LabsUser:
    """Transient user object for labs OAuth authentication.

    This class mimics Django's User interface but is never saved to the database.
    It's instantiated from session data on each request.
    """

    def __init__(self, user_data: dict[str, Any]) -> None:
        """Initialize from session user profile data."""
        self.id: int = user_data["id"]
        self.pk: int = user_data["id"]
        self.username: str = user_data["username"]
        self.email: str = user_data["email"]
        self.first_name: str = user_data.get("first_name", "")
        self.last_name: str = user_data.get("last_name", "")
        self.is_authenticated: bool = True
        self.is_active: bool = True
        self.is_staff: bool = False
        self.is_superuser: bool = False
        self.is_anonymous: bool = False

    @property
    def memberships(self):
        """Return empty queryset for organization memberships.

        LabsUser is not stored in database, so has no memberships.
        This property exists for compatibility with Django User interface.
        Could be populated with actual memberships in the future if needed.
        """
        return _get_empty_memberships()

    def __str__(self) -> str:
        return self.username

    def __repr__(self) -> str:
        return f"<LabsUser: {self.username}>"

    def get_full_name(self) -> str:
        """Return the first_name plus the last_name, with a space in between."""
        full_name = f"{self.first_name} {self.last_name}"
        return full_name.strip()

    def get_short_name(self) -> str:
        """Return the short name for the user."""
        return self.first_name

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Prevent saving to database."""
        raise NotImplementedError("LabsUser cannot be saved to database")

    def delete(self, *args: Any, **kwargs: Any) -> None:
        """Prevent deletion from database."""
        raise NotImplementedError("LabsUser cannot be deleted from database")
