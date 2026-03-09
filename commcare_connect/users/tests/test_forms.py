"""
Module for all Form Tests.
"""
from django.utils.translation import gettext_lazy as _

from commcare_connect.users.forms import ManualUserOTPForm, UserAdminCreationForm
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
