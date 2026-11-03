"""
Module for all Form Tests.
"""

import pytest
from allauth.core import context
from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.contrib.messages.middleware import MessageMiddleware
from django.contrib.sessions.middleware import SessionMiddleware
from django.utils.translation import gettext_lazy as _

from commcare_connect.users.forms import ManualUserOTPForm, UserAdminCreationForm, UserSignupForm
from commcare_connect.users.models import User


class TestUserAdminCreationForm:
    """
    Test class for all tests related to the UserAdminCreationForm
    """

    def test_username_validation_error_msg(self, user: User):
        """
        Tests UserAdminCreation Form's unique validator functions correctly by testing:
            1) A new user with an existing username cannot be added.
            2) Only 1 error is raised by the UserCreation Form
            3) The desired error message is raised
        """

        # The user already exists,
        # hence cannot be created.
        form = UserAdminCreationForm(
            {
                "email": user.email,
                "password1": user.password,
                "password2": user.password,
            }
        )

        assert not form.is_valid()
        assert len(form.errors) == 1
        assert "__all__" in form.errors
        assert form.errors["__all__"][0] == _("Constraint “unique_user_email” is violated.")


class TestManualUserOTPForm:
    def test_valid_phone_number(self):
        form = ManualUserOTPForm(data={"phone_number": "+1234567890"})
        assert form.is_valid()

    def test_phone_number_must_start_with_plus(self):
        form = ManualUserOTPForm(data={"phone_number": "1234567890"})
        assert not form.is_valid()
        assert form.errors.get("phone_number") == ["Phone number must start with a '+'."]

    def test_phone_number_must_be_numeric(self):
        form = ManualUserOTPForm(data={"phone_number": "+1234567abc"})
        assert not form.is_valid()
        assert form.errors.get("phone_number") == ["Phone number must be numeric."]


def _anonymous_post(rf, data=None):
    request = rf.post("/", data or {})
    request.user = AnonymousUser()
    SessionMiddleware(lambda r: None).process_request(request)
    MessageMiddleware(lambda r: None).process_request(request)
    return request


class TestUserSignupForm:
    def test_honeypot_field_is_rendered_off_screen(self):
        field = UserSignupForm().honeypot_field
        assert field.name == settings.ACCOUNT_SIGNUP_FORM_HONEYPOT_FIELD
        assert "-99999px" in str(field)
        assert 'tabindex="-1"' in str(field)

    def test_no_honeypot_field_when_setting_is_unset(self, settings):
        settings.ACCOUNT_SIGNUP_FORM_HONEYPOT_FIELD = None
        assert UserSignupForm().honeypot_field is None

    @pytest.mark.django_db
    def test_bot_that_fills_the_honeypot_gets_no_account(self, rf):
        data = {
            "email": "bot@example.com",
            "password1": "bots-c4nt-read-css",
            "password2": "bots-c4nt-read-css",
            settings.ACCOUNT_SIGNUP_FORM_HONEYPOT_FIELD: "https://spam.example",
        }
        form = UserSignupForm(data)
        assert form.is_valid(), form.errors
        request = _anonymous_post(rf, data)
        with context.request_context(request):
            user, response = form.try_save(request)
        assert user is None
        assert response.status_code == 302  # looks like the real "verification sent" redirect to the bot
        assert not User.objects.filter(email="bot@example.com").exists()
