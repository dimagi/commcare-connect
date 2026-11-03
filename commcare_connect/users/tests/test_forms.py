"""
Module for all Form Tests.
"""

import pytest
from allauth.account.forms import ResetPasswordForm
from allauth.core import context
from django.conf import settings
from django.template.loader import render_to_string
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
    def test_signup_template_renders_the_honeypot_field(self, anonymous_request):
        html = render_to_string(
            "account/signup.html", {"form": UserSignupForm(), "login_url": "/accounts/login/"}, anonymous_request()
        )
        assert f'name="{settings.ACCOUNT_SIGNUP_FORM_HONEYPOT_FIELD}"' in html

    @pytest.mark.django_db
    def test_bot_that_fills_the_honeypot_gets_no_account(self, anonymous_request):
        data = {
            "email": "bot@example.com",
            "password1": "bots-c4nt-read-css",
            "password2": "bots-c4nt-read-css",
            settings.ACCOUNT_SIGNUP_FORM_HONEYPOT_FIELD: "https://spam.example",
        }
        form = UserSignupForm(data)
        assert form.is_valid(), form.errors
        request = anonymous_request("post", "/accounts/signup/", data)
        with context.request_context(request):
            user, response = form.try_save(request)
        assert user is None
        assert response.status_code == 302  # looks like the real "verification sent" redirect to the bot
        assert not User.objects.filter(email="bot@example.com").exists()


@pytest.mark.django_db
def test_password_reset_does_not_mail_addresses_without_an_account(anonymous_request, mailoutbox):
    form = ResetPasswordForm({"email": "nobody@example.com"})
    assert form.is_valid(), form.errors  # enumeration prevention: the form never reveals the address is unknown
    request = anonymous_request("post", "/accounts/password/reset/")
    with context.request_context(request):
        form.save(request)
    assert mailoutbox == []
