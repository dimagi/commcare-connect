from crispy_forms import helper, layout
from django import forms
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db.models import Prefetch
from django.utils.html import format_html
from django.utils.translation import gettext, gettext_lazy

from commcare_connect.opportunity.forms import CHECKBOX_CLASS
from commcare_connect.organization.models import LLOEntity, Organization, OrganizationInvite
from commcare_connect.users.models import User
from commcare_connect.utils.forms import CreatableModelChoiceField, DynamicCreatableChoiceField
from commcare_connect.utils.permission_const import ORG_MANAGEMENT_SETTINGS_ACCESS, WORKSPACE_ENTITY_MANAGEMENT_ACCESS

LLO_ENTITY_SHORT_NAME_HELP_TEXT = gettext_lazy(
    "A brief abbreviation for the entity. This will be used to reference the organization in the Connect application."
)


class OrganizationChangeForm(forms.ModelForm):
    llo_entity = forms.ChoiceField(
        choices=[(None, gettext("No LLO Entity linked."))], label=gettext("LLO Entity"), required=False, disabled=True
    )

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

        layout_fields.append(layout.Field("llo_entity"))
        instance_llo = getattr(self.instance, "llo_entity", None)
        if self.user.has_perm(WORKSPACE_ENTITY_MANAGEMENT_ACCESS):
            self.fields["llo_entity"] = CreatableModelChoiceField(
                label=gettext("LLO Entity"),
                queryset=LLOEntity.objects.order_by("name"),
                widget=forms.Select(attrs={"x-ref": "llo_entity"}),
                empty_label=gettext("Select a LLO Entity"),
                required=False,
                create_key_name="name",
            )
            self.fields["llo_entity"].initial = instance_llo
            self.fields["llo_entity_short_name"] = forms.CharField(
                label=format_html(
                    '{} <span class="asteriskField" x-show="isNewEntity" x-cloak>*</span>',
                    gettext("LLO Entity Short Name"),
                ),
                max_length=40,
                required=False,
                widget=forms.TextInput(attrs={"x-ref": "llo_entity_short_name", ":required": "isNewEntity"}),
                help_text=LLO_ENTITY_SHORT_NAME_HELP_TEXT,
            )
            layout_fields.append(
                layout.Div(
                    layout.Field("llo_entity_short_name"),
                    **{"x-show": "isNewEntity", "x-cloak": True, "x-transition": True},
                )
            )
        else:
            if instance_llo:
                self.fields["llo_entity"].choices = [(self.instance.llo_entity_id, str(self.instance.llo_entity))]

        self.helper = helper.FormHelper(self)
        self.helper.layout = layout.Layout(
            *layout_fields,
            layout.Div(
                layout.Submit("submit", gettext("Update"), css_class="button button-md primary-dark"),
                css_class="flex justify-end",
            ),
        )

    def clean_llo_entity(self):
        if self.user.has_perm(WORKSPACE_ENTITY_MANAGEMENT_ACCESS):
            return self.cleaned_data["llo_entity"]
        return self.instance.llo_entity

    def clean(self):
        cleaned_data = super().clean()
        llo_entity = cleaned_data.get("llo_entity")
        if llo_entity and not llo_entity.pk and not cleaned_data.get("llo_entity_short_name"):
            self.add_error("llo_entity_short_name", gettext("This field is required when creating a new LLO Entity."))
        return cleaned_data

    def save(self, commit=True):
        org = super().save(commit=False)
        llo_entity = self.cleaned_data.get("llo_entity")
        short_name = self.cleaned_data.get("llo_entity_short_name") or None

        if self.user.has_perm(WORKSPACE_ENTITY_MANAGEMENT_ACCESS):
            if llo_entity and not llo_entity.pk:
                llo_entity.short_name = short_name
                if commit:
                    llo_entity.save()
            org.llo_entity = llo_entity

        if commit:
            org.save()
        return org


class OrganizationInviteForm(forms.ModelForm):
    class Meta:
        model = OrganizationInvite
        fields = ("email", "role")
        labels = {"email": "", "role": ""}
        widgets = {"email": forms.EmailInput(attrs={"placeholder": "Enter email address"})}

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
        email = self.cleaned_data["email"]
        if User.objects.filter(email__iexact=email, memberships__organization=self.organization).exists():
            raise ValidationError(gettext("A member with this email already belongs to this workspace."))
        if OrganizationInvite.objects.filter(
            organization=self.organization,
            email__iexact=email,
            status=OrganizationInvite.Status.invited,
            date_created__gte=OrganizationInvite.expiry_cutoff(),
        ).exists():
            raise ValidationError(gettext("This email already has a pending invite."))
        # Normalize so the case-sensitive unique_pending_org_invite constraint is effective.
        return email.lower()


class InviteSignupForm(forms.Form):
    """Password fields for a brand-new invitee creating their account inline.

    The email is fixed by the invite (the recipient proved ownership by clicking
    the emailed link), so it is not a form field here.
    """

    password1 = forms.CharField(widget=forms.PasswordInput(attrs={"placeholder": gettext_lazy("Password")}))
    password2 = forms.CharField(widget=forms.PasswordInput(attrs={"placeholder": gettext_lazy("Confirm Password")}))
    agree = forms.BooleanField(
        required=True,
        error_messages={"required": gettext_lazy("You must accept the Privacy Policy and Acceptable Use Policy.")},
    )

    def clean_password1(self):
        password = self.cleaned_data["password1"]
        validate_password(password)
        return password

    def clean(self):
        cleaned_data = super().clean()
        password1 = cleaned_data.get("password1")
        password2 = cleaned_data.get("password2")
        if password1 and password2 and password1 != password2:
            self.add_error("password2", gettext("The two password fields didn't match."))
        return cleaned_data


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


class OrganizationSelectOrCreateForm(forms.Form):
    llo_entity = CreatableModelChoiceField(
        label=gettext_lazy("LLO Entity"),
        queryset=LLOEntity.objects.order_by("name"),
        widget=forms.Select(attrs={"x-ref": "llo_entity"}),
        empty_label=gettext_lazy("Select a LLO Entity"),
        create_key_name="name",
    )
    llo_entity_short_name = forms.CharField(
        label=format_html(
            '{} <span class="asteriskField" x-show="isNewEntity" x-cloak>*</span>',
            gettext_lazy("LLO Entity Short Name"),
        ),
        max_length=40,
        required=False,
        widget=forms.TextInput(attrs={":required": "isNewEntity"}),
        help_text=LLO_ENTITY_SHORT_NAME_HELP_TEXT,
    )
    org = DynamicCreatableChoiceField(
        queryset=Organization.objects.order_by("name"),
        create_key_name="name",
        widget=forms.Select(attrs={"x-ref": "org"}),
        label=gettext_lazy("Workspace Name"),
        help_text=gettext_lazy(
            "This would be used to create the Workspace URL, and you will not be able to change the URL in future."
        ),
    )

    def get_entity_wise_orgs(self):
        data = {}
        qs = (
            LLOEntity.objects.prefetch_related(
                Prefetch("organization_set", queryset=Organization.objects.only("id", "name", "slug"))
            )
            .only("id", "name")
            .order_by("name")
        )

        for entity in qs:
            data[str(entity.id)] = {
                "organizations": [
                    {"id": org.id, "name": org.name, "slug": org.slug} for org in entity.organization_set.all()
                ]
            }
        return data

    def clean(self):
        cleaned_data = super().clean()
        org = cleaned_data.get("org")
        llo_entity = cleaned_data.get("llo_entity")
        if org and org.pk:
            if llo_entity and org.llo_entity != llo_entity:
                raise ValidationError(
                    {
                        "llo_entity": gettext(
                            "Selected LLO Entity does not match the existing organization's LLO Entity."
                        )
                    }
                )
        if llo_entity and not llo_entity.pk and not cleaned_data.get("llo_entity_short_name"):
            self.add_error("llo_entity_short_name", gettext("This field is required when creating a new LLO Entity."))
        return cleaned_data

    def save(self, commit=True):
        org = self.cleaned_data["org"]
        llo_entity = self.cleaned_data["llo_entity"]
        is_new_org = not org.pk
        org.llo_entity = llo_entity
        if commit:
            if llo_entity and not llo_entity.pk:
                if short_name := self.cleaned_data.get("llo_entity_short_name") or None:
                    llo_entity.short_name = short_name
                llo_entity.save()
            if is_new_org:
                org.save()
        return org, is_new_org
