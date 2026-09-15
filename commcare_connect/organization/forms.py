from allauth.account.forms import SetPasswordForm
from crispy_forms import helper, layout
from django import forms
from django.core.exceptions import ValidationError
from django.core.validators import EmailValidator, URLValidator
from django.utils import timezone
from django.utils.translation import gettext, gettext_lazy

from commcare_connect.opportunity.forms import CHECKBOX_CLASS
from commcare_connect.organization.models import (
    Organization,
    OrganizationInvite,
)
from commcare_connect.users.models import User
from commcare_connect.utils.permission_const import ORG_MANAGEMENT_SETTINGS_ACCESS

EARLIEST_ESTABLISHMENT_YEAR = 1800


class OrganizationChangeForm(forms.ModelForm):
    class Meta:
        model = Organization
        fields = ("name", "program_manager")
        labels = {
            "name": gettext_lazy("Workspace Name"),
            "program_manager": gettext_lazy("Enable Program Manager"),
        }

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user")
        super().__init__(*args, **kwargs)

        layout_fields = [layout.Field("name")]

        if self.user.has_perm(ORG_MANAGEMENT_SETTINGS_ACCESS):
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
        self.helper.form_tag = False
        self.helper.layout = layout.Layout(
            *layout_fields,
            layout.Div(
                layout.Submit("submit", gettext("Update"), css_class="button button-md primary-dark"),
                css_class="flex justify-end",
            ),
        )


class OrganizationProfileForm(forms.ModelForm):
    """Creates or edits a workspace and its organization profile."""

    class Meta:
        model = Organization
        fields = (
            "name",
            "short_name",
            "has_used_connect",
            "year_of_establishment",
            "team_size",
            "flws_managed",
            "countries",
            "regions",
            "primary_sectors",
            "website",
            "office_address",
            "contact_emails",
            "eoi_links",
            "notes",
        )
        widgets = {
            "countries": forms.SelectMultiple(
                attrs={"data-tomselect": "1", "placeholder": gettext_lazy("Select countries")}
            ),
            "primary_sectors": forms.SelectMultiple(
                attrs={"data-tomselect": "1", "placeholder": gettext_lazy("Select primary sectors")}
            ),
            "regions": forms.Textarea(attrs={"rows": 3}),
            "office_address": forms.Textarea(attrs={"rows": 3}),
            "contact_emails": forms.Textarea(attrs={"rows": 3}),
            "eoi_links": forms.Textarea(attrs={"rows": 3}),
            "notes": forms.Textarea(attrs={"rows": 4}),
        }
        labels = {
            "name": gettext_lazy("Workspace Name"),
            "short_name": gettext_lazy("Short Name"),
            "has_used_connect": gettext_lazy("Has Used CommCare Connect Before?"),
            "year_of_establishment": gettext_lazy("Year of Establishment"),
            "team_size": gettext_lazy("Team Size"),
            "flws_managed": gettext_lazy("Number of FLW's Managed"),
            "primary_sectors": gettext_lazy("Primary Sectors"),
            "office_address": gettext_lazy("Office Address"),
            "contact_emails": gettext_lazy("Contact Emails"),
            "eoi_links": gettext_lazy("EOI Links"),
        }
        help_texts = {
            "name": gettext_lazy(
                "This would be used to create the Workspace URL, and you will not be able to change the URL in future."
            ),
            "eoi_links": gettext_lazy("One Expression of Interest (EOI) link per line."),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = helper.FormHelper(self)
        self.helper.form_tag = False
        self.helper.disable_csrf = True
        self.helper.layout = layout.Layout(
            _wizard_step(
                1,
                gettext("Workspace"),
                "name",
                "short_name",
                layout.Field(
                    "has_used_connect",
                    wrapper_class="flex items-center gap-4 [&>label]:mb-0",
                ),
                "year_of_establishment",
                "website",
            ),
            _wizard_step(
                2,
                gettext("Operations"),
                "team_size",
                "flws_managed",
                "countries",
                "regions",
                "primary_sectors",
            ),
            _wizard_step(
                3,
                gettext("Contact & documents"),
                "office_address",
                "contact_emails",
                "eoi_links",
                "notes",
            ),
        )

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        duplicates = Organization.objects.filter(name__iexact=name)
        if self.instance.pk:
            duplicates = duplicates.exclude(pk=self.instance.pk)
        if duplicates.exists():
            raise ValidationError(gettext("A workspace with this name already exists."))
        return name

    def clean_contact_emails(self):
        return _clean_lines(
            self.cleaned_data.get("contact_emails", ""),
            EmailValidator(),
            gettext("Invalid email(s): %(bad)s"),
        )

    def clean_eoi_links(self):
        return _clean_lines(
            self.cleaned_data.get("eoi_links", ""),
            URLValidator(),
            gettext("Invalid URL(s): %(bad)s"),
        )

    def clean_year_of_establishment(self):
        return validate_year_of_establishment(self.cleaned_data.get("year_of_establishment"))


def _wizard_step(number, title, *fields):
    """Wraps a field group as one wizard step.

    `organizationWizard` shows one step at a time and lifts `data-step-title` into the step
    indicator, so the title is named once here rather than repeated as a heading in the form.
    """
    return layout.Div(
        layout.Fieldset("", *fields, aria_label=title),
        css_class="wizard-step",
        data_step_title=title,
        x_show=f"step === {number}",
        x_cloak=True,
    )


def validate_year_of_establishment(year):
    """Bounds the year against the calendar at call time, so the ceiling moves without a migration."""
    if year is None:
        return year
    current = timezone.now().year
    if year < EARLIEST_ESTABLISHMENT_YEAR or year > current:
        raise ValidationError(
            gettext("Year must be between %(earliest)s and %(current)s."),
            params={"earliest": EARLIEST_ESTABLISHMENT_YEAR, "current": current},
        )
    return year


def _clean_lines(raw, validator, error_message):
    """Validates one entry per line, returning them normalized back into a newline-separated string."""
    entries = [line.strip() for line in raw.splitlines() if line.strip()]
    bad = []
    for entry in entries:
        try:
            validator(entry)
        except ValidationError:
            bad.append(entry)
    if bad:
        raise ValidationError(error_message, params={"bad": ", ".join(bad)})
    return "\n".join(entries)


class OrganizationInviteForm(forms.ModelForm):
    email = forms.EmailField(
        max_length=254,
        required=True,
        label="",
        widget=forms.TextInput(attrs={"placeholder": "Enter email address"}),
    )

    class Meta:
        model = OrganizationInvite
        fields = ("email", "role")
        labels = {"role": ""}

    def __init__(self, *args, **kwargs):
        self.organization = kwargs.pop("organization")
        super().__init__(*args, **kwargs)

        self.helper = helper.FormHelper(self)
        self.helper.form_tag = False
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
        email = self.cleaned_data["email"].strip().lower()

        if User.objects.filter(email__iexact=email, memberships__organization=self.organization).exists():
            raise ValidationError(gettext("This person is already a member of this workspace."))

        existing = OrganizationInvite.objects.filter(organization=self.organization, email=email).first()
        if existing and existing.is_in_reinvite_cooldown:
            raise ValidationError(gettext("An invite was just sent to this address. Try again in a few minutes."))

        return email


class InviteAcceptForm(SetPasswordForm):
    """Sets a password for a brand-new user accepting an organization invite."""

    agree = forms.BooleanField(
        required=True,
        label=gettext_lazy("I agree to the Acceptable Use Policy"),
        error_messages={"required": gettext_lazy("You must agree to the Acceptable Use Policy to continue.")},
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
