from crispy_forms.helper import FormHelper
from crispy_forms.layout import Field, Layout, Row, Submit
from django import forms

from commcare_connect.opportunity.forms import OpportunityInitForm
from commcare_connect.program.models import ManagedOpportunity, Program

HALF_WIDTH_FIELD = "form-group col-md-6 mb-0"
DATE_INPUT = forms.DateInput(format="%Y-%m-%d", attrs={"type": "date", "class": "form-control"})


class ProgramForm(forms.ModelForm):
    class Meta:
        model = Program
        fields = [
            "name",
            "description",
            "delivery_type",
            "budget",
            "currency",
            "start_date",
            "end_date",
        ]
        widgets = {"start_date": DATE_INPUT, "end_date": DATE_INPUT}

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user", None)
        self.organization = kwargs.pop("organization")
        super().__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        self.helper.layout = Layout(
            Row(Field("name")),
            Row(Field("description")),
            Row(Field("delivery_type")),
            Row(
                Field("budget", wrapper_class=HALF_WIDTH_FIELD),
                Field("currency", wrapper_class=HALF_WIDTH_FIELD),
            ),
            Row(
                Field("start_date", wrapper_class=HALF_WIDTH_FIELD),
                Field("end_date", wrapper_class=HALF_WIDTH_FIELD),
            ),
            Submit("submit", "Submit"),
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


class ManagedOpportunityInitForm(OpportunityInitForm):
    class Meta(OpportunityInitForm.Meta):
        model = ManagedOpportunity

    def __init__(self, *args, **kwargs):
        self.program = kwargs.pop("program")
        super().__init__(*args, **kwargs)
        self.managed_opp = True

        # Managed opportunities should use the currency specified in the program.
        self.fields["currency"].initial = self.program.currency
        self.fields["currency"].widget = forms.TextInput(attrs={"readonly": "readonly", "disabled": True})
        self.fields["currency"].required = False

    def save(self, commit=True):
        self.instance.program = self.program
        self.instance.currency = self.program.currency
        return super().save(commit=commit)
