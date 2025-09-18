from allauth.account.forms import SignupForm
from allauth.socialaccount.forms import SignupForm as SocialSignupForm
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Field, Layout, Submit
from django import forms
from django.contrib.auth import forms as admin_forms
from django.contrib.auth import get_user_model
from django.forms import EmailField
from django.utils.translation import gettext_lazy as _

from commcare_connect.organization.models import Organization

User = get_user_model()


class UserAdminChangeForm(admin_forms.UserChangeForm):
    class Meta(admin_forms.UserChangeForm.Meta):
        model = User
        field_classes = {"email": EmailField}


class UserAdminCreationForm(admin_forms.UserCreationForm):
    """
    Form for User Creation in the Admin Area.
    To change user signup, see UserSignupForm and UserSocialSignupForm.
    """

    class Meta(admin_forms.UserCreationForm.Meta):
        model = User
        fields = ("email",)
        field_classes = {"email": EmailField}
        error_messages = {
            "email": {"unique": _("This email has already been taken.")},
        }


class UserSignupForm(SignupForm):
    """
    Form that will be rendered on a user sign up section/screen.
    Default fields will be added automatically.
    Check UserSocialSignupForm for accounts created from social.
    """


class UserSocialSignupForm(SocialSignupForm):
    """
    Renders the form when user has signed up using social accounts.
    Default fields will be added automatically.
    See UserSignupForm otherwise.
    """


class OrganizationCreationForm(forms.ModelForm):
    class Meta:
        model = Organization
        fields = ["name", "program_manager"]


class ManualUserOTPForm(forms.Form):
    phone_number = forms.CharField(
        max_length=16,
        label="Phone Number",
        help_text="Enter the phone number of the user you wish to retreive the OTP for.",
        widget=forms.TextInput(attrs={"placeholder": "e.g. +1234567890"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.helper = FormHelper(self)
        self.helper.layout = Layout(
            Field("phone_number"),
            Submit(
                name="submit",
                value="Retrieve OTP",
                css_class="button button-md primary-dark !inline-flex items-center",
            ),
        )

    def clean_phone_number(self):
        phone_number = self.cleaned_data.get("phone_number")
        phone_number = phone_number.strip().replace(" ", "")

        if not phone_number.startswith("+"):
            raise forms.ValidationError("Phone number must start with a '+'.")
        try:
            int(phone_number[1:])
        except ValueError:
            raise forms.ValidationError("Phone number must be numeric.")

        return phone_number
