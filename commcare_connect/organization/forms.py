from crispy_forms import helper, layout
from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext, gettext_lazy

from commcare_connect.opportunity.forms import CHECKBOX_CLASS
from commcare_connect.organization.models import LLOEntity, Organization, UserOrganizationMembership
from commcare_connect.users.models import User
from commcare_connect.utils.forms import CreatableModelChoiceField
from commcare_connect.utils.permission_const import ORG_MANAGEMENT_SETTINGS_ACCESS, WORKSPACE_ENTITY_MANAGEMENT_ACCESS


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

        instance_llo = getattr(self.instance, "llo_entity", None)
        if self.user.has_perm(WORKSPACE_ENTITY_MANAGEMENT_ACCESS):
            self.fields["llo_entity"] = CreatableModelChoiceField(
                label=gettext("LLO Entity"),
                queryset=LLOEntity.objects.order_by("name"),
                widget=forms.Select(),
                empty_label=gettext("Select a LLO Entity"),
                required=False,
                create_key_name="name",
            )
            self.fields["llo_entity"].initial = instance_llo
        else:
            if instance_llo:
                self.fields["llo_entity"].choices = [(self.instance.llo_entity_id, str(self.instance.llo_entity))]

        layout_fields.append(layout.Field("llo_entity"))

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

    def save(self, commit=True):
        org = super().save(commit=False)
        llo_entity = self.cleaned_data.get("llo_entity")

        org.llo_entity = llo_entity
        if commit:
            if llo_entity and not llo_entity.pk:
                llo_entity.save()
            org.save()
        return org


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


class OrganizationCreationForm(forms.Form):
    llo_entity = CreatableModelChoiceField(
        label=gettext_lazy("LLO Entity"),
        queryset=LLOEntity.objects.order_by("name"),
        widget=forms.Select(attrs={"x-ref": "llo_entity"}),
        empty_label=gettext_lazy("Select a LLO Entity"),
        create_key_name="name",
    )
    org = forms.CharField(
        label=gettext_lazy("Workspace Name"),
        widget=forms.Select(
            attrs={"x-ref": "org", "data-tomselect": "1", "data-tomselect:settings": '{"create": true}'}
        ),
        help_text=gettext_lazy(
            "This would be used to create the Workspace URL, and you will not be able to change the URL in future."
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.is_new_org = False

    def get_entity_wise_orgs(self):
        data = {}
        qs = LLOEntity.objects.prefetch_related("organization_set").only("id", "name").order_by("name")
        for entity in qs:
            data[str(entity.id)] = {
                "organizations": [
                    {"id": org.id, "name": org.name, "slug": org.slug} for org in entity.organization_set.all()
                ]
            }
        return data

    def clean_org(self):
        value = self.cleaned_data["org"]
        # Existing org selected (id)
        if value.isdigit():
            org = Organization.objects.filter(pk=value).first()
            if org:
                return org
        self.is_new_org = True
        return Organization(name=value)

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
        return cleaned_data

    def save(self, commit=True):
        org = self.cleaned_data["org"]
        llo_entity = self.cleaned_data["llo_entity"]
        org.llo_entity = llo_entity
        if commit:
            if llo_entity and not llo_entity.pk:
                llo_entity.save()
            if not org.pk:
                org.save()
        return org
