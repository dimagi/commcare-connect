from django import forms
from django.core.exceptions import ValidationError
from django.utils.timezone import localdate
from django.utils.translation import gettext_lazy as _

from commcare_connect.utils.datetime import DateRanges


class PresetDateRangeField(forms.MultiValueField):
    """A date range picked from relative presets ("Last 3 months") or entered as a custom From/To range.

    Cleans to an inclusive `slice(date_from, date_to)`, the range value django-filter's RangeFilter takes (either
    bound of a custom range may be None), or to None when no range is chosen. Presets are resolved against today
    when the form is cleaned, so a saved URL stays relative. The submitted values are `<name>_range`, `<name>_from`
    and `<name>_to`.
    """

    def __init__(self, *, presets, **kwargs):
        choices = [("", _("Any time"))] + [(p.value, p.label) for p in (*presets, DateRanges.CUSTOM)]
        fields = (
            forms.ChoiceField(choices=choices, required=False),
            forms.DateField(required=False),
            forms.DateField(required=False),
        )
        kwargs.setdefault("required", False)
        kwargs.setdefault("widget", PresetDateRangeWidget(choices=choices))
        super().__init__(fields, require_all_fields=False, **kwargs)

    def compress(self, data_list):
        preset, date_from, date_to = data_list or ("", None, None)
        if not preset:
            return None
        if preset != DateRanges.CUSTOM:
            return slice(*DateRanges(preset).bounds(localdate()))
        if not (date_from or date_to):
            return None
        if date_from and date_to and date_from > date_to:
            raise ValidationError(_("The From date must be on or before the To date."), code="invalid_range")
        return slice(date_from, date_to)


class PresetDateRangeWidget(forms.MultiWidget):
    """A preset dropdown, with From/To date inputs that Alpine shows (and enables) only for "Custom range"."""

    template_name = "widgets/preset_date_range.html"

    def __init__(self, choices, attrs=None):
        # Styled here because crispy-tailwind only styles the widgets it recognises by class name.
        # Hidden From/To inputs are disabled too, so a preset doesn't send stale custom dates.
        custom_only = {"type": "date", "class": "base-input", "x-bind:disabled": f"preset !== '{DateRanges.CUSTOM}'"}
        widgets = {
            "range": forms.Select(choices=choices, attrs={"class": "base-input", "x-model": "preset"}),
            "from": forms.DateInput(format="%Y-%m-%d", attrs=custom_only),
            "to": forms.DateInput(format="%Y-%m-%d", attrs=custom_only),
        }
        super().__init__(widgets, attrs)

    def decompress(self, value):
        if not value:
            return ["", None, None]
        return [DateRanges.CUSTOM.value, value.start, value.stop]

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        context["custom"] = DateRanges.CUSTOM.value
        return context
