from crispy_forms.helper import FormHelper
from crispy_forms.layout import Button, Field, Layout, Row, Submit
from django import forms
from django.utils.translation import gettext_lazy as _
from waffle import switch_is_active

from commcare_connect.flags.switch_names import ENABLE_PROGRAM_ACCESS_REDESIGN
from commcare_connect.opportunity.models import Country, Currency
from commcare_connect.organization.models import Organization
from commcare_connect.program.helpers import eligible_funders
from commcare_connect.program.models import Program

DATE_INPUT = forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"})


class ProgramForm(forms.ModelForm):
    currency = forms.ModelChoiceField(
        label="Currency",
        queryset=Currency.objects.order_by("code"),
        widget=forms.Select(attrs={"data-tomselect": "1"}),
        empty_label="Select a currency",
    )
    country = forms.ModelChoiceField(
        label="Country",
        queryset=Country.objects.order_by("name"),
        widget=forms.Select(attrs={"data-tomselect": "1"}),
        empty_label="Select a country",
    )
    funder = forms.ModelChoiceField(
        label=_("Funder"),
        queryset=Organization.objects.none(),
        required=False,
        widget=forms.Select(attrs={"data-tomselect": "1"}),
        empty_label=_("Select a funder"),
    )

    class Meta:
        model = Program
        fields = [
            "name",
            "description",
            "delivery_type",
            "funder",
            "budget",
            "currency",
            "country",
            "start_date",
            "end_date",
        ]
        widgets = {"start_date": DATE_INPUT, "end_date": DATE_INPUT, "description": forms.Textarea}

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user", None)
        self.organization = kwargs.pop("organization")
        super().__init__(*args, **kwargs)
        self.program_access_redesign_enabled = switch_is_active(ENABLE_PROGRAM_ACCESS_REDESIGN)
        if self.program_access_redesign_enabled:
            self._configure_funder_field()
        else:
            self.fields.pop("funder", None)
        self.helper = FormHelper(self)
        self.helper.form_tag = False
        self.helper.layout = Layout(*self._layout_fields())

    def _configure_funder_field(self):
        self.fields["funder"].queryset = eligible_funders(self.organization)
        if self.instance.pk:
            self._lock_funder_field()

    def _lock_funder_field(self):
        """The funder is chosen once, at creation.

        Django ignores submitted data for a disabled field and falls back to the instance
        value, so this is the enforcement, not merely a visual lock.
        """
        self.fields["funder"].disabled = True
        self.fields["funder"].widget.attrs.pop("data-tomselect", None)

    def _layout_fields(self):
        layout_fields = [
            Field("name"),
            Field("description"),
            Field("delivery_type"),
            Row(
                Field("budget"),
                Field("currency"),
                Field("country"),
                css_class="grid grid-cols-2 gap-2",
            ),
            Row(
                Field("start_date"),
                Field("end_date"),
                css_class="grid grid-cols-2 gap-2",
            ),
        ]
        if self.program_access_redesign_enabled:
            layout_fields.append(
                Row(
                    Field("funder"),
                    css_class="grid grid-cols-2 gap-2",
                )
            )
        layout_fields.append(
            Row(
                Button(
                    "close",
                    "Close",
                    css_class="button button-md outline-style",
                    **{"@click": "showProgramAddModal = showProgramEditModal = false"},
                ),
                Submit("submit", "Submit", css_class="button button-md primary-dark"),
                css_class="flex gap-3 justify-end mt-4",
            )
        )
        return layout_fields

    def clean(self):
        cleaned_data = super().clean()
        start_date = cleaned_data.get("start_date")
        end_date = cleaned_data.get("end_date")

        if start_date and end_date and end_date <= start_date:
            self.add_error("end_date", "End date must be after the start date.")
        return cleaned_data

    def save(self, commit=True):
        if not self.instance.pk:
            self.instance.organization = self.organization
            self.instance.created_by = self.user.email
        self.instance.modified_by = self.user.email
        return super().save(commit=commit)
