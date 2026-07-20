from crispy_forms.helper import FormHelper
from crispy_forms.layout import Button, Field, Layout, Row, Submit
from django import forms

from commcare_connect.opportunity.models import Country, Currency
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

    class Meta:
        model = Program
        fields = [
            "name",
            "description",
            "delivery_type",
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
        self.helper = FormHelper(self)
        self.helper.layout = Layout(
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
            Row(
                Button(
                    "close",
                    "Close",
                    css_class="button button-md outline-style",
                    **{"@click": "showProgramAddModal = showProgramEditModal = false"},
                ),
                Submit("submit", "Submit", css_class="button button-md primary-dark"),
                css_class="flex gap-3 justify-end mt-4",
            ),
        )

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
