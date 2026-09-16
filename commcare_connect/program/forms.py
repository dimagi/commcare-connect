from crispy_forms.helper import FormHelper
from crispy_forms.layout import Button, Field, Layout, Row, Submit
from django import forms
from django.utils.translation import gettext_lazy as _
from waffle import switch_is_active

from commcare_connect.flags.switch_names import ENABLE_PROGRAM_ACCESS_REDESIGN
from commcare_connect.opportunity.models import Country, Currency
from commcare_connect.organization.models import Organization
from commcare_connect.program.helpers import eligible_funders, eligible_watchers
from commcare_connect.program.models import Program

DATE_INPUT = forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"})


class ProgramForm(forms.ModelForm):
    currency = forms.ModelChoiceField(
        label=_("Currency"),
        queryset=Currency.objects.order_by("code"),
        widget=forms.Select(attrs={"data-tomselect": "1"}),
        empty_label=_("Select a currency"),
    )
    country = forms.ModelChoiceField(
        label=_("Country"),
        queryset=Country.objects.order_by("name"),
        widget=forms.Select(attrs={"data-tomselect": "1"}),
        empty_label=_("Select a country"),
    )
    funder = forms.ModelChoiceField(
        label=_("Funder"),
        queryset=Organization.objects.none(),
        required=False,
        widget=forms.Select(attrs={"data-tomselect": "1"}),
        empty_label=_("Select a funder"),
    )
    watchers = forms.ModelMultipleChoiceField(
        label=_("Watchers"),
        queryset=Organization.objects.none(),
        required=False,
        widget=forms.SelectMultiple(attrs={"data-tomselect": "1"}),
    )

    class Meta:
        model = Program
        fields = [
            "name",
            "description",
            "delivery_type",
            "funder",
            "watchers",
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
            self._configure_watchers_field()
        else:
            self.fields.pop("funder", None)
            self.fields.pop("watchers", None)
        self.helper = FormHelper(self)
        self.helper.form_tag = False
        self.helper.layout = Layout(*self._layout_fields())

    def _configure_funder_field(self):
        self.fields["funder"].queryset = eligible_funders(self.organization)
        if self.instance.pk:
            self._lock_funder_field()

    def _configure_watchers_field(self):
        self.fields["watchers"].queryset = eligible_watchers(self.organization, self.instance.funder)

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
                    Field("watchers"),
                    css_class="grid grid-cols-2 gap-2",
                )
            )
        layout_fields.append(
            Row(
                Button(
                    "close",
                    _("Close"),
                    css_class="button button-md outline-style",
                    **{"@click": "showProgramAddModal = showProgramEditModal = false"},
                ),
                Submit("submit", _("Submit"), css_class="button button-md primary-dark"),
                css_class="flex gap-3 justify-end mt-4",
            )
        )
        return layout_fields

    def clean(self):
        cleaned_data = super().clean()
        self._validate_dates(cleaned_data)
        self._validate_funder_is_not_watcher(cleaned_data)
        return cleaned_data

    def _validate_dates(self, cleaned_data):
        start_date = cleaned_data.get("start_date")
        end_date = cleaned_data.get("end_date")

        if start_date and end_date and end_date <= start_date:
            self.add_error("end_date", _("End date must be after the start date."))

    def _validate_funder_is_not_watcher(self, cleaned_data):
        funder = cleaned_data.get("funder")
        watchers = cleaned_data.get("watchers")

        if funder and watchers and funder in watchers:
            self.add_error("watchers", _("An organization cannot be both the funder and a watcher."))

    def save(self, commit=True):
        if not self.instance.pk:
            self.instance.organization = self.organization
            self.instance.created_by = self.user.email
        self.instance.modified_by = self.user.email
        return super().save(commit=commit)
