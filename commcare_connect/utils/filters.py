"""Building blocks for django-filter FilterSets."""

import django_filters
from crispy_forms.helper import FormHelper
from django import forms

from commcare_connect.utils.forms import PresetDateRangeField


class PresetDateRangeFilter(django_filters.RangeFilter):
    field_class = PresetDateRangeField


class CSRFExemptForm(forms.Form):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.disable_csrf = True
        self.helper.form_tag = False
