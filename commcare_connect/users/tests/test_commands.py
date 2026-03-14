import pytest
from django.core.management import call_command
from django.core.management.base import CommandError


@pytest.mark.django_db
class TestPromoteUserToSuperuser:
    def test_promotes_user(self, user):
        assert not user.is_superuser
        assert not user.is_staff

        call_command("promote_user_to_superuser", user.email)

        user.refresh_from_db()
        assert user.is_superuser
        assert user.is_staff

    def test_raises_error_for_unknown_email(self):
        with pytest.raises(CommandError, match="No user with email"):
            call_command("promote_user_to_superuser", "nobody@example.com")
