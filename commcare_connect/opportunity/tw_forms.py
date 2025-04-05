from crispy_forms.helper import FormHelper
from crispy_forms.layout import HTML, Column, Field, Fieldset, Layout, Row
from django import forms
from django.core.exceptions import ValidationError

from .models import DeliverUnit, DeliverUnitFlagRules, FormJsonValidationRules, OpportunityVerificationFlags

BASE_INPUT_CLASS = "base-input"
SELECT_CLASS = "base-dropdown"
TEXTAREA_CLASS = "simple-textarea"
CHECKBOX_CLASS = "simple-toggle"


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
        help_texts = {
            "location": "Minimum distance between form locations (metres)",
            "duplicate": "Flag duplicate form submissions for an entity.",
            "gps": "Flag forms with no location information.",
            "catchment_areas": "Flag forms outside a user's assigned catchment area",
        } 
        labels = {
            "duplicate": "Check Duplicates",
            "gps": "Check GPS",
            "form_submission_start": "Start Time",
            "form_submission_end": "End Time",
            "location": "Location Distance",
            "catchment_areas": "Catchment Area",
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
                Column(
                        Field("duplicate", wrapper_class="grid grid-cols-[1fr_auto] gap-x-4 w-full"),
                        Field("gps", wrapper_class="grid grid-cols-[1fr_auto] gap-x-4 w-full"),
                        Field("catchment_areas", wrapper_class="grid grid-cols-[1fr_auto] gap-x-4 w-full"),  
                ),Column(
                        Row(Field("location"),css_class="flex-1"),
                        Fieldset(
                            "Form Submission Hours",
                            Row(
                                Column(Field("form_submission_start"),css_class="flex-1"),
                                Column(Field("form_submission_end"),css_class="flex-1"),
                                css_class="flex gap-4 w-full"
                            ),
                        )
                ),
                css_class="grid grid-cols-2 gap-6"
            )
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
                    Column(
                        HTML("<div class='font-medium text-sm'>Attachments</div>"),
                        Field("check_attachments",wrapper_class="flex gap-4 w-full"),
                        css_class="flex flex-col"
                    ),
                    css_class="flex flex-col"
                ),
                Column(Field("duration")),
                css_class="grid grid-cols-3 gap-x-6"
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
                Row(
                    Column(Field("name")),
                    Column(Field("question_path")),
                    Column(Field("question_value")),
                    css_class="flex flex-col gap-2"
                ),
                Column(Field("deliver_unit", wrapper_class="w-full")),
                css_class="grid grid-cols-2 gap-6"
            )
        )
