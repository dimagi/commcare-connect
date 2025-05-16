from crispy_forms.helper import FormHelper
from crispy_forms.layout import Button, Field, Layout, Row, Submit
from django import forms

from commcare_connect.opportunity.forms import OpportunityInitForm
from commcare_connect.organization.models import Organization
from commcare_connect.program.models import ManagedOpportunity, Program, ProgramApplicationStatus

DATE_INPUT = forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"})


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
                css_class="float-end",
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
        self.instance.currency = self.cleaned_data["currency"].upper()
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

        program_members = Organization.objects.filter(
            programapplication__program=self.program, programapplication__status=ProgramApplicationStatus.ACCEPTED
        ).distinct()

        self.fields["organization"] = forms.ModelChoiceField(
            queryset=program_members,
            required=True,
            widget=forms.Select(attrs={"class": "form-control"}),
            label="Network Manager Organization",
        )

        self.helper.layout.fields.insert(3, Row(Field("organization")))

    def save(self, commit=True):
        self.instance.program = self.program
        self.instance.currency = self.program.currency
        return super().save(commit=commit)
