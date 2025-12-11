from crispy_forms.helper import FormHelper
from crispy_forms.layout import Button, Column, Field, Layout, Row, Submit
from django import forms

from commcare_connect.opportunity.forms import OpportunityInitForm, OpportunityInitUpdateForm
from commcare_connect.opportunity.models import Country, Currency
from commcare_connect.organization.models import Organization
from commcare_connect.program.models import ManagedOpportunity, Program, ProgramApplicationStatus

DATE_INPUT = forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"})


class ProgramForm(forms.ModelForm):
    currency_fk = forms.ModelChoiceField(
        label="Currency",
        queryset=Currency.objects.filter(is_valid=True).order_by("code"),
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
            "currency_fk",
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
                Field("currency_fk"),
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
        return super().save(commit=commit)


class BaseManagedOpportunityInitForm:
    managed_opp = True

    def __init__(self, *args, **kwargs):
        self.program = kwargs.pop("program")
        super().__init__(*args, **kwargs)

        # Managed opportunities should use the currency/country specified in the program.
        for field_name in ["currency_fk", "country"]:
            form_field = self.fields[field_name]
            form_field.initial = getattr(self.program, field_name)
            form_field.widget.attrs.update({"readonly": "readonly", "disabled": True})
            form_field.required = False

        program_members = Organization.objects.filter(
            programapplication__program=self.program, programapplication__status=ProgramApplicationStatus.ACCEPTED
        ).distinct()

        self.fields["organization"] = forms.ModelChoiceField(
            queryset=program_members,
            required=True,
            widget=forms.Select(attrs={"class": "form-control"}),
            label="Network Manager Organization",
        )
        self.set_organization_initial()
        opportunity_details_row = self.helper.layout[0]
        organization_field_layout = Column(
            Field("organization"), css_class="col-span-2"  # This makes the field take the full width of the grid row
        )
        opportunity_details_row.fields.insert(1, organization_field_layout)

    def set_organization_initial(self):
        pass

    def save(self, commit=True):
        self.instance.program = self.program
        self.instance.currency_fk = self.program.currency_fk
        self.instance.delivery_type = self.program.delivery_type
        return super().save(commit=commit)


class ManagedOpportunityInitForm(BaseManagedOpportunityInitForm, OpportunityInitForm):
    class Meta(OpportunityInitForm.Meta):
        model = ManagedOpportunity


class ManagedOpportunityInitUpdateForm(BaseManagedOpportunityInitForm, OpportunityInitUpdateForm):
    class Meta(OpportunityInitUpdateForm.Meta):
        model = ManagedOpportunity

    def set_organization_initial(self):
        self.fields["organization"].initial = self.instance.organization
