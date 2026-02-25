from crispy_forms.helper import FormHelper
from crispy_forms.layout import Column, Field, Layout, Row, Submit
from django import forms
from django.contrib.auth import get_user_model

from commcare_connect.flags.models import Flag
from commcare_connect.opportunity.models import Opportunity
from commcare_connect.organization.models import Organization
from commcare_connect.program.models import Program

User = get_user_model()


class FlagForm(forms.ModelForm):
    users = forms.ModelMultipleChoiceField(
        queryset=User.objects.order_by("name", "username"),
        required=False,
        widget=forms.SelectMultiple(attrs={"data-tomselect": "1"}),
        label="Users",
    )
    organizations = forms.ModelMultipleChoiceField(
        queryset=Organization.objects.order_by("name"),
        required=False,
        widget=forms.SelectMultiple(attrs={"data-tomselect": "1"}),
        label="Workspaces",
    )
    programs = forms.ModelMultipleChoiceField(
        queryset=Program.objects.order_by("name"),
        required=False,
        widget=forms.SelectMultiple(attrs={"data-tomselect": "1"}),
        label="Programs",
    )
    opportunities = forms.ModelMultipleChoiceField(
        queryset=Opportunity.objects.order_by("name"),
        required=False,
        widget=forms.SelectMultiple(attrs={"data-tomselect": "1"}),
        label="Opportunities",
    )

    class Meta:
        model = Flag
        fields = ["users", "organizations", "programs", "opportunities"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        self.helper.form_tag = False
        self.helper.layout = Layout(
            Row(
                Column(Field("users"), css_class="col-span-1"),
                Column(Field("organizations"), css_class="col-span-1"),
                Column(Field("programs"), css_class="col-span-1"),
                Column(Field("opportunities"), css_class="col-span-1"),
                css_class="grid grid-cols-1 sm:grid-cols-2 gap-4",
            ),
            Row(
                Submit("submit", "Save", css_class="button button-md primary-dark !inline-flex items-center gap-2"),
                css_class="flex justify-end mt-3",
            ),
        )
