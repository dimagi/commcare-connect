from crispy_forms import helper, layout
from django import forms
from django.utils.translation import gettext

from commcare_connect.organization.models import Organization, UserOrganizationMembership
from commcare_connect.users.models import User


class OrganizationChangeForm(forms.ModelForm):
    class Meta:
        model = Organization
        fields = ("name",)
        labels = {
            "name": gettext("Organization Name"),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.helper = helper.FormHelper(self)
        self.helper.layout = layout.Layout(
            layout.Row(layout.Field("name")),
            layout.Submit("submit", gettext("Update")),
        )


class MembershipForm(forms.ModelForm):
    class Meta:
        model = UserOrganizationMembership
        fields = ("user", "role")
        labels = {"user": "", "role": ""}

    def __init__(self, *args, **kwargs):
        self.organization = kwargs.pop("organization")
        super().__init__(*args, **kwargs)

        self.fields["user"].queryset = User.objects.filter(email__isnull=False).exclude(
            memberships__organization=self.organization
        )

        self.helper = helper.FormHelper(self)
        self.helper.layout = layout.Layout(
            layout.Row(
                layout.HTML("<h4>Add new member</h4>"),
                layout.Field("user", wrapper_class="col-md-5"),
                layout.Field("role", wrapper_class="col-md-5"),
                layout.Div(layout.Submit("submit", gettext("Submit")), css_class="col-md-2"),
            ),
        )


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
            layout.Row(layout.Field("credential")),
            layout.Row(layout.Field("users")),
            layout.Row(layout.Div(layout.Submit("submit", gettext("Submit")))),
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
