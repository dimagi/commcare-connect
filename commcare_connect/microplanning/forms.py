from crispy_forms.helper import FormHelper
from crispy_forms.layout import Field, Fieldset, Layout, Submit
from django import forms
from django.utils.translation import gettext_lazy as _


class AreaSelectionForm(forms.Form):
    wards = forms.MultipleChoiceField(
        choices=[],
        widget=forms.SelectMultiple(
            attrs={
                "data-tomselect": "1",
                "x-model": "selectedWards",
            },
        ),
        required=False,
        label=_("Select Wards"),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["wards"].choices = self.get_ward_choices()

        self.helper = FormHelper(self)
        self.helper.layout = Layout(
            Fieldset(
                _("Area Selection"),
                Field("wards"),
            ),
            Submit("submit", "Load Wards", css_class="button button-md primary-dark"),
        )

    def get_ward_choices(self):
        # Todo: fetch real data when available
        return []
