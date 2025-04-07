from crispy_forms.helper import FormHelper
from crispy_forms.layout import HTML, Column, Field, Fieldset, Layout, Row, Submit
from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils.timezone import now

from commcare_connect.connect_id_client.models import Credential
from commcare_connect.opportunity.forms import FILTER_COUNTRIES

from .models import (
    DeliverUnit,
    DeliverUnitFlagRules,
    FormJsonValidationRules,
    Opportunity,
    OpportunityVerificationFlags,
    PaymentUnit,
)

BASE_INPUT_CLASS = "w-full p-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500"
SELECT_CLASS = "w-full p-2 border border-gray-300 rounded-lg"
TEXTAREA_CLASS = "w-full p-2 border border-gray-300 rounded-lg"
CHECKBOX_CLASS = "form-checkbox text-blue-600 rounded"


class OpportunityVerificationFlagsConfigForm(forms.ModelForm):
    class Meta:
        model = OpportunityVerificationFlags
        fields = (
            "duplicate",
            "gps",
            "location",
            "form_submission_start",
            "form_submission_end",
            "catchment_areas",
        )
        widgets = {
            "form_submission_start": forms.TimeInput(attrs={"type": "time", "class": BASE_INPUT_CLASS}),
            "form_submission_end": forms.TimeInput(attrs={"type": "time", "class": BASE_INPUT_CLASS}),
            "location": forms.NumberInput(attrs={"class": BASE_INPUT_CLASS}),
        }
        labels = {
            "duplicate": "Check Duplicates",
            "gps": "Check GPS",
            "form_submission_start": "Start Time",
            "form_submission_end": "End Time",
            "location": "Location Distance",
            "catchment_areas": "Catchment Area",
        }
        help_texts = {
            "location": "Minimum distance between form locations (metres)",
            "duplicate": "Flag duplicate form submissions for an entity.",
            "gps": "Flag forms with no location information.",
            "catchment_areas": "Flag forms outside a user's assigned catchment area",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["duplicate"].widget.attrs["class"] = CHECKBOX_CLASS
        self.fields["gps"].widget.attrs["class"] = CHECKBOX_CLASS
        self.fields["catchment_areas"].widget.attrs["class"] = CHECKBOX_CLASS

        self.helper = FormHelper(self)
        self.helper.form_tag = False
        self.helper.layout = Layout(
            Row(
                Field("duplicate", wrapper_class="flex items-center gap-2"),
                Field("gps", wrapper_class="flex items-center gap-2"),
                Field("catchment_areas", wrapper_class="flex items-center gap-2"),
            ),
            Row(Field("location")),
            Fieldset(
                "Form Submission Hours",
                Row(
                    Column(Field("form_submission_start")),
                    Column(Field("form_submission_end")),
                ),
            ),
        )

        for field in ["duplicate", "location", "gps", "catchment_areas"]:
            self.fields[field].required = False

        if self.instance:
            self.fields["form_submission_start"].initial = self.instance.form_submission_start
            self.fields["form_submission_end"].initial = self.instance.form_submission_end


class DeliverUnitFlagsForm(forms.ModelForm):
    class Meta:
        model = DeliverUnitFlagRules
        fields = ("deliver_unit", "check_attachments", "duration")
        help_texts = {"duration": "Minimum time to complete form (minutes)"}
        labels = {"check_attachments": "Require Attachments"}

    def __init__(self, *args, **kwargs):
        self.opportunity = kwargs.pop("opportunity")
        super().__init__(*args, **kwargs)

        self.fields["deliver_unit"] = forms.ModelChoiceField(
            queryset=DeliverUnit.objects.filter(app=self.opportunity.deliver_app),
            disabled=True,
            empty_label=None,
            widget=forms.Select(attrs={"class": SELECT_CLASS}),
        )

        self.fields["check_attachments"].widget.attrs["class"] = CHECKBOX_CLASS
        self.fields["duration"].widget.attrs["class"] = BASE_INPUT_CLASS

        self.helper = FormHelper(self)
        self.helper.form_tag = False
        self.helper.layout = Layout(
            Row(
                Column(Field("deliver_unit")),
                Column(
                    HTML("<div class='font-semibold mb-2'>Attachments</div>"),
                    Field("check_attachments", wrapper_class="flex items-center gap-2"),
                ),
                Column(Field("duration")),
            ),
        )

    def clean_deliver_unit(self):
        deliver_unit = self.cleaned_data["deliver_unit"]
        if (
            self.instance.pk is None
            and DeliverUnitFlagRules.objects.filter(deliver_unit=deliver_unit, opportunity=self.opportunity).exists()
        ):
            raise ValidationError("Flags are already configured for this Deliver Unit.")
        return deliver_unit


class FormJsonValidationRulesForm(forms.ModelForm):
    class Meta:
        model = FormJsonValidationRules
        fields = ("name", "deliver_unit", "question_path", "question_value")
        widgets = {
            "name": forms.TextInput(attrs={"class": BASE_INPUT_CLASS}),
            "question_path": forms.TextInput(attrs={"class": BASE_INPUT_CLASS}),
            "question_value": forms.TextInput(attrs={"class": BASE_INPUT_CLASS}),
        }

    def __init__(self, *args, **kwargs):
        self.opportunity = kwargs.pop("opportunity")
        super().__init__(*args, **kwargs)

        self.fields["deliver_unit"] = forms.ModelChoiceField(
            queryset=DeliverUnit.objects.filter(app=self.opportunity.deliver_app),
            disabled=True,
            empty_label=None,
            widget=forms.Select(attrs={"class": "w-full p-2 border border-gray-300 rounded-lg"}),
        )

        self.helper = FormHelper(self)
        self.helper.form_tag = False
        self.helper.render_hidden_fields = True
        self.helper.layout = Layout(
            Row(
                Column(Field("name")),
                Column(Field("question_path")),
                Column(Field("question_value")),
            ),
            Row(Column(Field("deliver_unit", wrapper_class="space-y-2"))),
        )


class OpportunityUserInviteForm(forms.Form):
    def __init__(self, *args, **kwargs):
        org_slug = kwargs.pop("org_slug", None)
        credentials = [Credential(org_slug, org_slug)]  # connect_id_client.fetch_credentials(org_slug)
        super().__init__(*args, **kwargs)

        self.helper = FormHelper(self)
        self.helper.layout = Layout(
            Fieldset(
                "",
                Row(Field("users", css_class=TEXTAREA_CLASS)),
                Row(
                    Field("filter_country", css_class=BASE_INPUT_CLASS),
                    Field("filter_credential", css_class=BASE_INPUT_CLASS),
                ),
            ),
            Submit("submit", "Submit"),
        )

        self.fields["users"] = forms.CharField(
            widget=forms.Textarea,
            help_text="Enter the phone numbers of the users you want to add to this opportunity, one on each line.",
            required=False,
        )
        self.fields["filter_country"] = forms.CharField(
            label="Filter By Country",
            widget=forms.Select(choices=[("", "Select country")] + FILTER_COUNTRIES),
            required=False,
        )
        self.fields["filter_credential"] = forms.CharField(
            label="Filter By Credential",
            widget=forms.Select(choices=[("", "Select credential")] + [(c.slug, c.name) for c in credentials]),
            required=False,
        )
        self.initial["filter_country"] = [""]
        self.initial["filter_credential"] = [""]

    def clean_users(self):
        user_data = self.cleaned_data["users"]
        split_users = [line.strip() for line in user_data.splitlines() if line.strip()]
        return split_users


class OpportunityChangeForm(
    OpportunityUserInviteForm,
    forms.ModelForm,
):
    class Meta:
        model = Opportunity
        fields = [
            "name",
            "description",
            "active",
            "currency",
            "short_description",
            "is_test",
            "delivery_type",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.helper = FormHelper(self)
        self.helper.layout = Layout(
            Row(Field("name", css_class=BASE_INPUT_CLASS)),
            Row(Field("active", css_class=CHECKBOX_CLASS)),
            Row(Field("is_test", css_class=CHECKBOX_CLASS)),
            Row(Field("delivery_type", css_class=BASE_INPUT_CLASS)),
            Row(Field("description", css_class=TEXTAREA_CLASS)),
            Row(Field("short_description", css_class=BASE_INPUT_CLASS)),
            Row(Field("currency", css_class=SELECT_CLASS)),
            Row(
                Field("additional_users", css_class=BASE_INPUT_CLASS),
                Field("end_date", css_class=BASE_INPUT_CLASS),
            ),
            HTML("<hr />"),
            Fieldset(
                "Invite Users",
                Row(Field("users", css_class=TEXTAREA_CLASS)),
                Row(
                    Field("filter_country", css_class=BASE_INPUT_CLASS),
                    Field("filter_credential", css_class=BASE_INPUT_CLASS),
                ),
            ),
            Submit("submit", "Submit"),
        )

        self.fields["additional_users"] = forms.IntegerField(
            required=False, help_text="Adds budget for additional users."
        )
        self.fields["end_date"] = forms.DateField(
            widget=forms.DateInput(attrs={"type": "date", "class": "form-input"}),
            required=False,
            help_text="Extends opportunity end date for all users.",
        )
        if self.instance:
            if self.instance.end_date:
                self.initial["end_date"] = self.instance.end_date.isoformat()
            self.currently_active = self.instance.active

    def clean_active(self):
        active = self.cleaned_data["active"]
        if active and not self.currently_active:
            app_ids = (self.instance.learn_app.cc_app_id, self.instance.deliver_app.cc_app_id)
            if (
                Opportunity.objects.filter(active=True)
                .filter(Q(learn_app__cc_app_id__in=app_ids) | Q(deliver_app__cc_app_id__in=app_ids))
                .exists()
            ):
                raise ValidationError("Cannot reactivate opportunity with reused applications", code="app_reused")
        return active


class PaymentUnitForm(forms.ModelForm):
    class Meta:
        model = PaymentUnit
        fields = ["name", "description", "amount", "max_total", "max_daily", "start_date", "end_date"]
        help_texts = {
            "start_date": "Optional. If not specified opportunity start date applies to form submissions.",
            "end_date": "Optional. If not specified opportunity end date applies to form submissions.",
        }
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date", "class": BASE_INPUT_CLASS}),
            "end_date": forms.DateInput(attrs={"type": "date", "class": BASE_INPUT_CLASS}),
        }

    def __init__(self, *args, **kwargs):
        deliver_units = kwargs.pop("deliver_units", [])
        payment_units = kwargs.pop("payment_units", [])
        super().__init__(*args, **kwargs)

        self.helper = FormHelper(self)
        self.helper.layout = Layout(
            Row(Field("name", css_class=BASE_INPUT_CLASS)),
            Row(Field("description")),
            Row(Field("amount", css_class=BASE_INPUT_CLASS)),
            Row(Column("start_date"), Column("end_date")),
            Row(Field("required_deliver_units", css_class=CHECKBOX_CLASS)),
            Row(Field("optional_deliver_units", css_class=CHECKBOX_CLASS)),
            Row(Field("payment_units", css_class=CHECKBOX_CLASS)),
            Field("max_total", css_class=BASE_INPUT_CLASS),
            Field("max_daily", css_class=BASE_INPUT_CLASS),
            Submit(name="submit", value="Submit"),
        )
        deliver_unit_choices = [(deliver_unit.id, deliver_unit.name) for deliver_unit in deliver_units]
        payment_unit_choices = [(payment_unit.id, payment_unit.name) for payment_unit in payment_units]
        self.fields["required_deliver_units"] = forms.MultipleChoiceField(
            choices=deliver_unit_choices,
            widget=forms.CheckboxSelectMultiple,
            help_text="All of the selected Deliver Units are required for payment accrual.",
        )
        self.fields["optional_deliver_units"] = forms.MultipleChoiceField(
            choices=deliver_unit_choices,
            widget=forms.CheckboxSelectMultiple,
            help_text=(
                "Any one of these Deliver Units combined with all the required "
                "Deliver Units will accrue payment. Multiple Deliver Units can be selected."
            ),
            required=False,
        )
        self.fields["payment_units"] = forms.MultipleChoiceField(
            choices=payment_unit_choices,
            widget=forms.CheckboxSelectMultiple,
            help_text="The selected Payment Units need to be completed in order to complete this payment unit.",
            required=False,
        )
        if PaymentUnit.objects.filter(pk=self.instance.pk).exists():
            deliver_units = self.instance.deliver_units.all()
            self.fields["required_deliver_units"].initial = [
                deliver_unit.pk for deliver_unit in filter(lambda x: not x.optional, deliver_units)
            ]
            self.fields["optional_deliver_units"].initial = [
                deliver_unit.pk for deliver_unit in filter(lambda x: x.optional, deliver_units)
            ]
            payment_units_initial = []
            for payment_unit in payment_units:
                if payment_unit.parent_payment_unit_id and payment_unit.parent_payment_unit_id == self.instance.pk:
                    payment_units_initial.append(payment_unit.pk)
            self.fields["payment_units"].initial = payment_units_initial

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data:
            if cleaned_data["max_daily"] > cleaned_data["max_total"]:
                self.add_error(
                    "max_daily",
                    "Daily max visits per user cannot be greater than total Max visits per user",
                )
            common_deliver_units = set(cleaned_data.get("required_deliver_units", [])) & set(
                cleaned_data.get("optional_deliver_units", [])
            )
            for deliver_unit in common_deliver_units:
                deliver_unit_obj = DeliverUnit.objects.get(pk=deliver_unit)
                self.add_error(
                    "optional_deliver_units",
                    error=f"{deliver_unit_obj.name} cannot be marked both Required and Optional",
                )
            if cleaned_data["end_date"] and cleaned_data["end_date"] < now().date():
                self.add_error("end_date", "Please provide a valid end date.")
        return cleaned_data
