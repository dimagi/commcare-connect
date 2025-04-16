import datetime
import json

from crispy_forms.helper import FormHelper
from crispy_forms.layout import HTML, Column, Field, Fieldset, Layout, Row, Submit, Div
from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Q, Sum
from django.utils.timezone import now

from commcare_connect.connect_id_client.models import Credential
from commcare_connect.opportunity.forms import FILTER_COUNTRIES, DateRanges
from commcare_connect.organization.models import Organization
from commcare_connect.program.models import ManagedOpportunity, Program

from .models import (
    CommCareApp,
    DeliverUnit,
    DeliverUnitFlagRules,
    FormJsonValidationRules,
    HQApiKey,
    Opportunity,
    OpportunityVerificationFlags,
    PaymentInvoice,
    PaymentUnit,
    VisitValidationStatus,
)

BASE_INPUT_CLASS = "base-input"
SELECT_CLASS = "base-dropdown"
TEXTAREA_CLASS = "base-textarea"
CHECKBOX_CLASS = "simple-toggle"


class TailwindFormHelper(FormHelper):
    def __init__(self, form=None):
        super().__init__(form)

    def render_layout(self, form, context, template_pack="tailwind"):
        return super().render_layout(form, context, "tailwind")


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

        self.helper = TailwindFormHelper(self)
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

        self.helper = TailwindFormHelper(self)
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

def icon_field(field_name, icon_class, **kwargs):
    icon_html = f'<i class="fa {icon_class} mr-2 text-gray-500"></i>'
    return Field(
        field_name,
        label=Markup(icon_html + field_name.capitalize()),
        **kwargs
    )


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

        # self.fields["deliver_unit"] = forms.ModelChoiceField(
        #     queryset=DeliverUnit.objects.filter(app=self.opportunity.deliver_app),
        #     disabled=True,
        #     empty_label=None,
        #     widget=forms.Select(attrs={"class": "w-full p-2 border border-gray-300 rounded-lg"}),
        # )

        self.fields["deliver_unit"] = forms.ModelMultipleChoiceField(
            queryset=DeliverUnit.objects.filter(app=self.opportunity.deliver_app),
            widget=forms.CheckboxSelectMultiple(attrs={"class": "space-y-2"}),  # Tailwind spacing between checkboxes
            disabled=True,
        )

        self.helper = TailwindFormHelper(self)
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


class OpportunityUserInviteForm(forms.Form):
    def __init__(self, *args, **kwargs):
        org_slug = kwargs.pop("org_slug", None)
        credentials = [Credential(org_slug, org_slug)]  # connect_id_client.fetch_credentials(org_slug)
        super().__init__(*args, **kwargs)

        self.helper = TailwindFormHelper(self)
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

        self.helper = TailwindFormHelper(self)
        self.helper.layout = Layout(
            Row(
                HTML("<div class='col-span-2'><h6 class='title-sm'>Opportunity Details</h6>  <span class='hint'>Edit the details of the opportunity. All fields are mandatory.  </span> </div>"),
                Column(
                    Field("name", css_class=BASE_INPUT_CLASS,wrapper_class="w-full"),
                    Field("short_description", css_class=BASE_INPUT_CLASS,wrapper_class="w-full"),  
                    Field("description", css_class=TEXTAREA_CLASS,wrapper_class="w-full"),
                ),
                Column(  
                    Field("delivery_type", css_class=BASE_INPUT_CLASS),
                    Field("active", css_class=CHECKBOX_CLASS,wrapper_class="bg-slate-100 flex items-center justify-between p-4 rounded-lg"),
                    Field("is_test", css_class=CHECKBOX_CLASS,wrapper_class="bg-slate-100 flex items-center justify-between p-4 rounded-lg"), 
                ),
                css_class="grid grid-cols-2 gap-4 p-6 card_bg"
            ), 
            Row(
                HTML("<div class='col-span-2'><h6 class='title-sm'>Date</h6>  <span class='hint'>Optional: If not specified, the opportunity start & end dates will apply to the form submissions.</span> </div>"),
                Column(
                    Field("end_date", css_class=BASE_INPUT_CLASS),
                ),
                Column( 
                    Field("currency", css_class=SELECT_CLASS),
                    Field("additional_users", css_class=BASE_INPUT_CLASS)
                ),
                css_class="grid grid-cols-2 gap-4 p-6 card_bg"
            ),   
            Row(
                HTML("<div class='col-span-2'><h6 class='title-sm'>Invite Workers</h6></div>"),
                Row(
                    Field("filter_country", css_class=BASE_INPUT_CLASS,wrapper_class="w-full"),
                    Field("filter_credential", css_class=BASE_INPUT_CLASS,wrapper_class="w-full"),
                    css_class="flex gap-2"
                ),
                Row(Field("users", css_class=TEXTAREA_CLASS,wrapper_class="w-full"),css_class="col-span-2"),
                css_class="grid grid-cols-2 gap-4 p-6 card_bg"
            ),
            Row(
                Submit("submit", "Submit",css_class="button button-md primary-dark"),
                css_class="flex justify-end"
            ) 
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

        self.helper = TailwindFormHelper(self)
        self.helper.layout = Layout(
            Row(
                Column(
                    Field("name", css_class=BASE_INPUT_CLASS),
                    Field("description",css_class=TEXTAREA_CLASS),
                ),
                Column( 
                    Row(
                        Field("start_date", css_class=BASE_INPUT_CLASS),
                        Field("end_date", css_class=BASE_INPUT_CLASS),
                        css_class="grid grid-cols-2 gap-4"),
                    Row(
                        Field("max_total", css_class=BASE_INPUT_CLASS),
                        Field("max_daily", css_class=BASE_INPUT_CLASS),
                        css_class="grid grid-cols-2 gap-4"
                    ),
                    Field("amount", css_class=BASE_INPUT_CLASS),
                ),
                css_class="grid grid-cols-2 gap-4"
            ),
            HTML('<div class="bg-gray-200 h-0.5 w-full my-8"></div>'),
            Row(
                Column(
                    Field("required_deliver_units", css_class=CHECKBOX_CLASS),
                    Field("optional_deliver_units", css_class=CHECKBOX_CLASS)
                ),
                Column(Field("payment_units", css_class=CHECKBOX_CLASS)),
                css_class="grid grid-cols-2 gap-4"
            ),
            Row(
                Submit(name="submit", value="Submit",css_class="button button-md primary-dark"),
                css_class="flex justify-end"
            )
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




class SendMessageMobileUsersForm(forms.Form):
    title = forms.CharField(
        empty_value="Notification from CommCare Connect",
        required=False,
    )
    body = forms.CharField(widget=forms.Textarea)
    message_type = forms.MultipleChoiceField(
        choices=[("notification", "Push Notification"), ("sms", "SMS")],
        widget=forms.CheckboxSelectMultiple,
    )

    def __init__(self, *args, **kwargs):
        users = kwargs.pop("users", [])
        super().__init__(*args, **kwargs)

        self.helper = TailwindFormHelper(self)
        self.helper.layout = Layout(
            Row(Field("selected_users", css_class=CHECKBOX_CLASS)),
            Row(Field("title", css_class=BASE_INPUT_CLASS)),
            Row(Field("body", css_class=TEXTAREA_CLASS)),
            Row(Field("message_type", css_class=CHECKBOX_CLASS)),
            Submit(name="submit", value="Submit",css_class="button button-md primary-dark"),
        )

        choices = [(user.pk, user.username) for user in users]
        self.fields["selected_users"] = forms.MultipleChoiceField(choices=choices)


class PaymentInvoiceForm(forms.ModelForm):
    class Meta:
        model = PaymentInvoice
        fields = ("amount", "date", "invoice_number", "service_delivery")
        widgets = {"date": forms.DateInput(attrs={"type": "date", "class": BASE_INPUT_CLASS})}

    def __init__(self, *args, **kwargs):
        self.opportunity = kwargs.pop("opportunity")
        super().__init__(*args, **kwargs)

        self.helper = TailwindFormHelper(self)
        self.helper.layout = Layout(
            Column(
                Row(
                    Row(
                        Div(
                            Field(
                                "amount",
                                css_class=f"{BASE_INPUT_CLASS} {'border-red-500 ring-2 ring-red-500/70  pr-8 mb-1' if self.errors.get('amount') else ''}",
                            ),
                            HTML(
                                '<i class="fas fa-exclamation-circle text-red-500 absolute right-2 top-1/2 transform -translate-y-1/2 mt-1"></i>'
                            ) if self.errors.get("amount") else HTML(""),
                            css_class="relative w-full",
                        ),
                        css_class="flex-1",
                    ),
                    Row(
                        Div(
                            Field(
                                "date",
                                css_class=f"{BASE_INPUT_CLASS} {'border-red-500 ring-2 ring-red-500/70 pr-8 mb-1' if self.errors.get('date') else ''}",
                            ),
                            HTML(
                                '<i class="fas fa-exclamation-circle text-red-500 absolute right-2 top-1/2 transform -translate-y-1/2 mt-1"></i>'
                            ) if self.errors.get("date") else HTML(""),
                            css_class="relative w-full",
                        ),
                        css_class="flex-1",
                    ),
                    Row(
                        Div(
                            Field(
                                "invoice_number",
                                css_class=f"{BASE_INPUT_CLASS} {'border-red-500 ring-2 ring-red-500/70  pr-8 mb-1' if self.errors.get('invoice_number') else ''}",
                            ),
                            HTML(
                                '<i class="fas fa-exclamation-circle text-red-500 absolute right-2 top-1/2 transform -translate-y-1/2 mt-1"></i>'
                            ) if self.errors.get("invoice_number") else HTML(""),
                            css_class="relative w-full",
                        ),
                        css_class="flex-1",
                    ),
                    Row(
                        Field(
                            "service_delivery",
                            css_class=CHECKBOX_CLASS,
                            wrapper_class="w-full bg-slate-100 flex items-center justify-between p-4 rounded-lg",
                        ),
                    ),
                    css_class="flex flex-col gap-2",
                ),
                Row(
                    Submit("cancel", "Cancel", css_class="button button-md outline-style"),
                    Submit("submit", "Submit", css_class="button button-md primary-dark"),
                    css_class="flex justify-end gap-4",
                ),
                css_class="flex flex-col gap-4",
            )
        )
        self.helper.form_tag = False

    def clean_invoice_number(self):
        invoice_number = self.cleaned_data["invoice_number"]
        if PaymentInvoice.objects.filter(opportunity=self.opportunity, invoice_number=invoice_number).exists():
            raise ValidationError(
                f'Invoice "{invoice_number}" already exists',
                code="invoice_number_reused",
            )
        return invoice_number

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.opportunity = self.opportunity
        if commit:
            instance.save()
        return instance


class OpportunityInitForm(forms.ModelForm):
    managed_opp = False

    class Meta:
        model = Opportunity
        fields = [
            "name",
            "description",
            "short_description",
            "currency",
        ]

    def __init__(self, *args, **kwargs):
        self.domains = kwargs.pop("domains", [])
        self.user = kwargs.pop("user", {})
        self.org_slug = kwargs.pop("org_slug", "")
        super().__init__(*args, **kwargs)

        self.helper = TailwindFormHelper(self)
        self.helper.layout = Layout(
            Row(
                HTML("<div class='col-span-2'><h6 class='title-sm'>Opportunity Details</h6>  <span class='hint'>Add the details of the opportunity. All fields are mandatory.</span> </div>"),
                Column(
                    Field("name", css_class=BASE_INPUT_CLASS),
                    Field("short_description", css_class=BASE_INPUT_CLASS),
                    Field("description", css_class=TEXTAREA_CLASS),  
                ), 
                Column( 
                    Field("currency", css_class=BASE_INPUT_CLASS),
                    Field("api_key", css_class=BASE_INPUT_CLASS),
                ),
                css_class="grid grid-cols-2 gap-4 card_bg"
            ),
            Row(
                HTML("<div class='col-span-2'><h6 class='title-sm'>Apps</h6>  <span class='hint'>Add required apps to the opportunity. All fields are mandatory.</span> </div>"),
                Column(
                    "Learn App",
                    Field("learn_app_domain", css_class=SELECT_CLASS),
                    Field("learn_app", css_class=SELECT_CLASS),
                    Field("learn_app_description", css_class=TEXTAREA_CLASS),
                    Field("learn_app_passing_score", css_class=BASE_INPUT_CLASS),
                    data_loading_states=True,
                ),  
                Column(
                    "Deliver App",
                    Field("deliver_app_domain", css_class=SELECT_CLASS),
                    Field("deliver_app", css_class=SELECT_CLASS), 
                    data_loading_states=True,
                ),  
                css_class="grid grid-cols-2 gap-4 card_bg my-4"
            ),  
            Row(
                Submit("submit", "Submit",css_class="button button-md primary-dark"),
                css_class="flex justify-end"
            )
        )

        domain_choices = [(i, f"Domain {i}") for i in range(1, 11)]
        self.fields["description"] = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}))
        self.fields["learn_app_domain"] = forms.ChoiceField(
            choices=domain_choices,
        )
        learn_app_choices = [(i, f"Learn App {i}") for i in range(1, 11)]
        self.fields["learn_app"] = forms.Field(widget=forms.Select(choices=learn_app_choices))
        self.fields["learn_app_description"] = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}))
        self.fields["learn_app_passing_score"] = forms.IntegerField(max_value=100, min_value=0)
        self.fields["deliver_app_domain"] = forms.ChoiceField(
            choices=domain_choices,
        )
        deliver_app_choices = [(i, f"Deliver App {i}") for i in range(1, 11)]
        self.fields["deliver_app"] = forms.Field(widget=forms.Select(choices=deliver_app_choices))
        self.fields["api_key"] = forms.CharField(max_length=50)

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data:
            cleaned_data["learn_app"] = json.loads(cleaned_data["learn_app"])
            cleaned_data["deliver_app"] = json.loads(cleaned_data["deliver_app"])

            if cleaned_data["learn_app"]["id"] == cleaned_data["deliver_app"]["id"]:
                self.add_error("learn_app", "Learn app and Deliver app cannot be same")
                self.add_error("deliver_app", "Learn app and Deliver app cannot be same")
            return cleaned_data

    def save(self, commit=True):
        organization = Organization.objects.get(slug=self.org_slug)
        learn_app = self.cleaned_data["learn_app"]
        deliver_app = self.cleaned_data["deliver_app"]
        learn_app_domain = self.cleaned_data["learn_app_domain"]
        deliver_app_domain = self.cleaned_data["deliver_app_domain"]

        self.instance.learn_app, _ = CommCareApp.objects.get_or_create(
            cc_app_id=learn_app["id"],
            cc_domain=learn_app_domain,
            organization=organization,
            defaults={
                "name": learn_app["name"],
                "created_by": self.user.email,
                "modified_by": self.user.email,
                "description": self.cleaned_data["learn_app_description"],
                "passing_score": self.cleaned_data["learn_app_passing_score"],
            },
        )
        self.instance.deliver_app, _ = CommCareApp.objects.get_or_create(
            cc_app_id=deliver_app["id"],
            cc_domain=deliver_app_domain,
            organization=organization,
            defaults={
                "name": deliver_app["name"],
                "created_by": self.user.email,
                "modified_by": self.user.email,
            },
        )
        self.instance.created_by = self.user.email
        self.instance.modified_by = self.user.email
        self.instance.currency = self.instance.currency.upper()

        if self.managed_opp:
            self.instance.organization = self.cleaned_data.get("organization")
        else:
            self.instance.organization = organization

        api_key, _ = HQApiKey.objects.get_or_create(user=self.user, api_key=self.cleaned_data["api_key"])
        self.instance.api_key = api_key
        super().save(commit=commit)

        return self.instance


class OpportunityFinalizeForm(forms.ModelForm):
    class Meta:
        model = Opportunity
        fields = [
            "start_date",
            "end_date",
            "total_budget",
        ]
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date", "class": BASE_INPUT_CLASS}),
            "end_date": forms.DateInput(attrs={"type": "date", "class": BASE_INPUT_CLASS}),
        }

    def __init__(self, *args, **kwargs):
        self.budget_per_user = kwargs.pop("budget_per_user")
        self.payment_units_max_total = kwargs.pop("payment_units_max_total", 0)
        self.opportunity = kwargs.pop("opportunity")
        self.current_start_date = kwargs.pop("current_start_date")
        self.is_start_date_readonly = self.current_start_date < datetime.date.today()
        super().__init__(*args, **kwargs)

        self.helper = TailwindFormHelper(self)
        self.helper.layout = Layout(
            Row(
                Field(
                        "start_date",
                        help="Start date can't be edited if it was set in past" if self.is_start_date_readonly else None,
                        wrapper_class="flex-1"
                    ),
                Field("end_date",wrapper_class="flex-1"),
                Field(
                        "max_users",
                        oninput=f"id_total_budget.value = ({self.budget_per_user} + {self.payment_units_max_total}"
                        f"* parseInt(document.getElementById('id_org_pay_per_visit')?.value || 0)) "
                        f"* parseInt(this.value || 0)",
                        css_class=BASE_INPUT_CLASS
                    ),
                    Field("total_budget", readonly=True, wrapper_class="form-group ", css_class=BASE_INPUT_CLASS),
                css_class="grid grid-cols-2 gap-6"
            ),
           Row(
             Submit("submit", "Submit",css_class="button button-md primary-dark"),
             css_class="flex justify-end"
           )
        )

        if self.opportunity.managed:
            self.helper.layout.fields.insert(
                -2,
                Row(
                    Field(
                        "org_pay_per_visit",
                        oninput=f"id_total_budget.value = ({self.budget_per_user} + {self.payment_units_max_total}"
                        f"* parseInt(this.value || 0)) "
                        f"* parseInt(document.getElementById('id_max_users')?.value || 0)",
                        css_class=BASE_INPUT_CLASS,
                    )
                ),
            )
            self.fields["org_pay_per_visit"] = forms.IntegerField(
                required=True, widget=forms.NumberInput(attrs={"class": "form-control"})
            )

        self.fields["max_users"] = forms.IntegerField()
        self.fields["start_date"].disabled = self.is_start_date_readonly
        self.fields["total_budget"].widget.attrs.update({"class": "form-control-plaintext"})

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data:
            if self.is_start_date_readonly:
                cleaned_data["start_date"] = self.current_start_date
            start_date = cleaned_data["start_date"]
            end_date = cleaned_data["end_date"]
            if end_date < now().date():
                self.add_error("end_date", "Please enter the correct end date for this opportunity")
            if not self.is_start_date_readonly and start_date < now().date():
                self.add_error("start_date", "Start date should be today or latter")
            if start_date >= end_date:
                self.add_error("end_date", "End date must be after start date")

            if self.opportunity.managed:
                managed_opportunity = self.opportunity.managedopportunity
                program = managed_opportunity.program
                if not (program.start_date <= start_date <= program.end_date):
                    self.add_error("start_date", "Start date must be within the program's start and end dates.")

                if not (program.start_date <= end_date <= program.end_date):
                    self.add_error("end_date", "End date must be within the program's start and end dates.")

                total_budget_sum = (
                    ManagedOpportunity.objects.filter(program=program)
                    .exclude(id=managed_opportunity.id)
                    .aggregate(total=Sum("total_budget"))["total"]
                    or 0
                )
                if total_budget_sum + cleaned_data["total_budget"] > program.budget:
                    self.add_error("total_budget", "Budget exceeds the program budget.")

            return cleaned_data


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
        widgets = {
            "start_date": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date", "class": BASE_INPUT_CLASS}),
            "end_date": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date", "class": BASE_INPUT_CLASS}),
        }

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user", None)
        self.organization = kwargs.pop("organization")
        super().__init__(*args, **kwargs)
        self.helper = TailwindFormHelper(self)
        self.helper.layout = Layout(
            Row(Field("name", css_class=BASE_INPUT_CLASS,wrapper_class="w-full")),
            Row(Field("description", css_class=TEXTAREA_CLASS,wrapper_class="w-full")),
            Row(Field("delivery_type", css_class=BASE_INPUT_CLASS,wrapper_class="w-full")),
            Row(
                Field("budget", css_class=BASE_INPUT_CLASS,wrapper_class="w-full"),
                Field("currency", css_class=BASE_INPUT_CLASS,wrapper_class="w-full"),
                css_class="flex gap-4 w-full"
            ),
            Row(
                Field("start_date",wrapper_class="w-full"),
                Field("end_date",wrapper_class="w-full"),
                css_class="flex gap-4 w-full"
            ),
            Row(
                Submit("submit", "Submit",css_class="button button-md primary-dark"),
                css_class="flex justify-end"
            )
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


class VisitExportForm(forms.Form):
    format = forms.ChoiceField(choices=(("csv", "CSV"), ("xlsx", "Excel")), initial="xlsx")
    date_range = forms.ChoiceField(choices=DateRanges.choices, initial=DateRanges.LAST_30_DAYS)
    status = forms.MultipleChoiceField(choices=[("all", "All")] + VisitValidationStatus.choices, initial=["all"])
    flatten_form_data = forms.BooleanField(initial=True, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = TailwindFormHelper(self)
        self.helper.layout = Layout(
            Row(
                Field("format", css_class=SELECT_CLASS),
                Field("date_range", css_class=SELECT_CLASS),
                Field("status", css_class=SELECT_CLASS),
                Field("flatten_form_data", css_class=CHECKBOX_CLASS, wrapper_class="flex p-4 justify-between rounded-lg bg-gray-100"),
                css_class="flex flex-col" 
            ),
        )
        self.helper.form_tag = False

    def clean_status(self):
        statuses = self.cleaned_data["status"]
        if not statuses or "all" in statuses:
            return []

        return [VisitValidationStatus(status) for status in statuses]
