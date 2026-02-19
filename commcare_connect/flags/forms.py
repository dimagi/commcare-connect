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
