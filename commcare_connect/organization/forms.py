from crispy_forms import helper, layout
from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext

from commcare_connect.opportunity.forms import CHECKBOX_CLASS
from commcare_connect.organization.models import Organization, UserOrganizationMembership
from commcare_connect.users.models import User
from commcare_connect.utils.permission_const import ORG_MANAGEMENT_SETTINGS_ACCESS


class OrganizationChangeForm(forms.ModelForm):
    class Meta:
        model = Organization
        fields = ("name", "program_manager")
        labels = {
            "name": gettext("Organization Name"),
            "program_manager": gettext("Enable Program Manager"),
        }

    def __init__(self, *args, **kwargs):
        user = kwargs.pop("user")
        super().__init__(*args, **kwargs)

        layout_fields = [layout.Field("name")]

        if user.has_perm(ORG_MANAGEMENT_SETTINGS_ACCESS):
            layout_fields.append(
                layout.Field(
                    "program_manager",
                    css_class=CHECKBOX_CLASS,
                    wrapper_class="bg-slate-100 flex items-center justify-between p-4 rounded-lg",
                )
            )
        else:
            del self.fields["program_manager"]

        self.helper = helper.FormHelper(self)
        self.helper.layout = layout.Layout(
            *layout_fields,
            layout.Div(
                layout.Submit("submit", gettext("Update"), css_class="button button-md primary-dark"),
                css_class="flex justify-end",
            ),
        )


class MembershipForm(forms.ModelForm):
    email = forms.CharField(
        max_length=254,
        required=True,
        label="",
        widget=forms.TextInput(attrs={"placeholder": "Enter email address"}),
    )

    class Meta:
        model = UserOrganizationMembership
        fields = ("role",)
        labels = {"role": ""}

    def __init__(self, *args, **kwargs):
        self.organization = kwargs.pop("organization")
        super().__init__(*args, **kwargs)

        self.helper = helper.FormHelper(self)
        self.helper.layout = layout.Layout(
            layout.Row(
                layout.Field("email", wrapper_class="col-md-5"),
                layout.Field("role", wrapper_class="col-md-5"),
                layout.Div(
                    layout.Submit("submit", gettext("Submit"), css_class="button button-md primary-dark float-end")
                ),
                css_class="flex flex-col",
            ),
        )

    def clean_email(self):
        email = self.cleaned_data["email"]
        user = User.objects.filter(email=email).exclude(memberships__organization=self.organization).first()

        if not user:
            raise ValidationError("User with this email does not exist or is already a member")

        self.instance.user = user
        return email


class AddCredentialForm(forms.Form):
    credential = forms.CharField(widget=forms.Select)
    users = forms.CharField(
        widget=forms.Textarea(
            attrs=dict(
                placeholder="Enter the phone numbers of the users you want to add the "
                "credential to, one on each line.",
            )
        ),
    )

    def __init__(self, *args, **kwargs):
        credentials = kwargs.pop("credentials", [])
        super().__init__(*args, **kwargs)

        self.fields["credential"].widget.choices = [(c.name, c.name) for c in credentials]

        self.helper = helper.FormHelper(self)
        self.helper.layout = layout.Layout(
            layout.Row(
                layout.Field("credential"),
                layout.Field("users"),
                layout.Div(
                    layout.Submit("submit", gettext("Submit"), css_class="button button-md primary-dark float-end")
                ),
                css_class="flex flex-col",
            ),
        )

    def clean_users(self):
        user_data = self.cleaned_data["users"]
        split_users = [line.strip() for line in user_data.splitlines() if line.strip()]
        return split_users


OrganizationCreationForm = forms.modelform_factory(
    Organization,
    fields=("name",),
    labels={"name": "Organization Name"},
    help_texts={
        "name": (
            "This would be used to create the Organization URL,"
            " and you will not be able to change the URL in future."
        )
    },
)
