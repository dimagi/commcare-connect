import datetime
import json

from crispy_forms.helper import FormHelper
from crispy_forms.layout import HTML, Column, Div, Field, Fieldset, Layout, Row, Submit
from dateutil.relativedelta import relativedelta
from django import forms
from django.core.exceptions import ValidationError
from django.db.models import F, Q, Sum, TextChoices
from django.urls import reverse
from django.utils.timezone import now

from commcare_connect.opportunity.models import (
    CommCareApp,
    DeliverUnit,
    DeliverUnitFlagRules,
    FormJsonValidationRules,
    HQApiKey,
    Opportunity,
    OpportunityAccess,
    OpportunityClaim,
    OpportunityClaimLimit,
    OpportunityVerificationFlags,
    PaymentInvoice,
    PaymentUnit,
    VisitReviewStatus,
    VisitValidationStatus,
)
from commcare_connect.organization.models import Organization
from commcare_connect.program.models import ManagedOpportunity
from commcare_connect.users.models import User

FILTER_COUNTRIES = [("+276", "Malawi"), ("+234", "Nigeria"), ("+27", "South Africa"), ("+91", "India")]

CHECKBOX_CLASS = "simple-toggle"


class HQApiKeyCreateForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.helper = FormHelper(self)
        self.helper.layout = Layout(
            Field("hq_server"),
            Field("api_key"),
            Submit("submit", "Save", css_class="button button-md primary-dark float-end"),
        )
        self.helper.form_tag = False

    class Meta:
        model = HQApiKey
        fields = ("hq_server", "api_key")


class OpportunityUserInviteForm(forms.Form):
    def __init__(self, *args, **kwargs):
        self.opportunity = kwargs.pop("opportunity", None)
        super().__init__(*args, **kwargs)

        self.helper = FormHelper(self)
        self.helper.layout = Layout(
            Field("users"),
            Submit("submit", "Submit", css_class="button button-md primary-dark float-end"),
        )
        self.fields["users"] = forms.CharField(
            widget=forms.Textarea,
            required=False,
            help_text="Enter the phone numbers of the users you want to add to this opportunity, one on each line.",
        )

    def clean_users(self):
        user_data = self.cleaned_data["users"]

        if user_data and self.opportunity and not self.opportunity.is_setup_complete:
            raise ValidationError("Please finish setting up the opportunity before inviting users.")

        split_users = [line.strip() for line in user_data.splitlines() if line.strip()]
        return split_users


class OpportunityChangeForm(OpportunityUserInviteForm, forms.ModelForm):
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
        self.opportunity = self.instance

        self.helper = FormHelper(self)
        self.helper.layout = Layout(
            Row(
                HTML(
                    "<div class='col-span-2'>"
                    "<h6 class='title-sm'>Opportunity Details</h6>"
                    "<span class='hint'>Edit the details of the opportunity. All fields are mandatory.</span>"
                    "</div>"
                ),
                Column(
                    Field("name", wrapper_class="w-full"),
                    Field("short_description", wrapper_class="w-full"),
                    Field("description", wrapper_class="w-full"),
                ),
                Column(
                    Field("delivery_type"),
                    Field(
                        "active",
                        css_class=CHECKBOX_CLASS,
                        wrapper_class="bg-slate-100 flex items-center justify-between p-4 rounded-lg",
                    ),
                    Field(
                        "is_test",
                        css_class=CHECKBOX_CLASS,
                        wrapper_class="bg-slate-100 flex items-center justify-between p-4 rounded-lg",
                    ),
                ),
                css_class="grid grid-cols-2 gap-4 p-6 card_bg",
            ),
            Row(
                HTML(
                    "<div class='col-span-2'>"
                    "<h6 class='title-sm'>Date</h6>"
                    "<span class='hint'>Optional: If not specified, the opportunity start & end dates will"
                    " apply to the form submissions.</span>"
                    "</div>"
                ),
                Column(
                    Field("end_date"),
                ),
                Column(Field("currency"), Field("additional_users")),
                css_class="grid grid-cols-2 gap-4 p-6 card_bg",
            ),
            Row(
                HTML("<div class='col-span-2'><h6 class='title-sm'>Invite Workers</h6></div>"),
                Row(Field("users", wrapper_class="w-full"), css_class="col-span-2"),
                css_class="grid grid-cols-2 gap-4 p-6 card_bg",
            ),
            Row(Submit("submit", "Submit", css_class="button button-md primary-dark"), css_class="flex justify-end"),
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


class OpportunityInitForm(forms.ModelForm):
    managed_opp = False

    class Meta:
        model = Opportunity
        fields = [
            "name",
            "description",
            "short_description",
            "currency",
            "hq_server",
        ]

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user", {})
        self.org_slug = kwargs.pop("org_slug", "")
        super().__init__(*args, **kwargs)

        self.helper = FormHelper(self)
        self.helper.layout = Layout(
            Row(
                HTML(
                    "<div class='col-span-2'>"
                    "<h6 class='title-sm'>Opportunity Details</h6>"
                    "<span class='hint'>Add the details of the opportunity. All fields are mandatory.</span>"
                    "</div>"
                ),
                Column(
                    Field("name"),
                    Field("short_description"),
                    Field("description"),
                ),
                Column(
                    Field("currency"),
                    Field("hq_server"),
                    Column(
                        Field("api_key", wrapper_class="flex-1"),
                        HTML(
                            "<button class='button-icon primary-dark'"
                            "type='button' @click='showAddApiKeyModal = true'>"
                            "<i class='fa-regular fa-plus'></i>"
                            "</button>"
                        ),
                        css_class="flex items-center gap-1",
                    ),
                ),
                css_class="grid grid-cols-2 gap-4 card_bg",
            ),
            Row(
                HTML(
                    "<div class='col-span-2'>"
                    "<h6 class='title-sm'>Apps</h6>"
                    "<span class='hint'>Add required apps to the opportunity. All fields are mandatory.</span>"
                    "</div>"
                ),
                Column(
                    Field("learn_app_domain"),
                    Field("learn_app"),
                    Field("learn_app_description"),
                    Field("learn_app_passing_score"),
                    data_loading_states=True,
                ),
                Column(
                    Field("deliver_app_domain"),
                    Field("deliver_app"),
                    data_loading_states=True,
                ),
                css_class="grid grid-cols-2 gap-4 card_bg my-4",
            ),
            Row(Submit("submit", "Submit", css_class="button button-md primary-dark"), css_class="flex justify-end"),
        )

        self.fields["description"] = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}))

        def get_htmx_swap_attrs(url_query: str, include: str, trigger: str):
            return {
                "hx-get": reverse(url_query, args=(self.org_slug,)),
                "hx-include": include,
                "hx-trigger": trigger,
                "hx-target": "this",
                "data-loading-disable": True,
            }

        def get_domain_select_attrs():
            return get_htmx_swap_attrs(
                "opportunity:get_domains",
                "#id_hq_server, #id_api_key",
                "change from:#id_api_key",
            )

        def get_app_select_attrs(app_type: str):
            domain_select_id = f"#id_{app_type}_app_domain"
            return get_htmx_swap_attrs(
                "opportunity:get_applications_by_domain",
                f"#id_hq_server, {domain_select_id}, #id_api_key",
                f"change from:{domain_select_id}",
            )

        self.fields["learn_app_domain"] = forms.Field(
            widget=forms.Select(
                choices=[(None, "Select an API key to load domains.")],
                attrs=get_domain_select_attrs(),
            ),
        )
        self.fields["learn_app"] = forms.Field(
            widget=forms.Select(choices=[(None, "Loading...")], attrs=get_app_select_attrs("learn"))
        )
        self.fields["learn_app_description"] = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}))
        self.fields["learn_app_passing_score"] = forms.IntegerField(max_value=100, min_value=0)

        self.fields["deliver_app_domain"] = forms.Field(
            widget=forms.Select(
                choices=[(None, "Select an API key to load domains.")],
                attrs=get_domain_select_attrs(),
            ),
        )
        self.fields["deliver_app"] = forms.Field(
            widget=forms.Select(choices=[(None, "Loading...")], attrs=get_app_select_attrs("deliver"))
        )

        self.fields["api_key"] = forms.Field(
            widget=forms.Select(
                choices=[(None, "Select a HQ Server to load API Keys.")],
                attrs=get_htmx_swap_attrs(
                    "opportunity:get_api_keys",
                    "#id_hq_server",
                    "change from:#id_hq_server, reload_api_keys from:body",
                ),
            ),
        )

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data:
            try:
                cleaned_data["learn_app"] = json.loads(cleaned_data["learn_app"])
                cleaned_data["deliver_app"] = json.loads(cleaned_data["deliver_app"])

                if cleaned_data["learn_app"]["id"] == cleaned_data["deliver_app"]["id"]:
                    self.add_error("learn_app", "Learn app and Deliver app cannot be same")
                    self.add_error("deliver_app", "Learn app and Deliver app cannot be same")
            except KeyError:
                raise forms.ValidationError("Invalid app data")
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
            hq_server=self.instance.hq_server,
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
            hq_server=self.instance.hq_server,
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

        api_key, _ = HQApiKey.objects.get_or_create(
            id=self.cleaned_data["api_key"],
            defaults={
                "hq_server": self.cleaned_data["hq_server"],
                "user": self.user,
            },
        )
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
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        self.budget_per_user = kwargs.pop("budget_per_user")
        self.payment_units_max_total = kwargs.pop("payment_units_max_total", 0)
        self.opportunity = kwargs.pop("opportunity")
        self.current_start_date = kwargs.pop("current_start_date")
        self.is_start_date_readonly = self.current_start_date < datetime.date.today()
        super().__init__(*args, **kwargs)

        self.helper = FormHelper(self)
        self.helper.layout = Layout(
            Row(
                Field(
                    "start_date",
                    help="Start date can't be edited if it was set in past" if self.is_start_date_readonly else None,
                    wrapper_class="flex-1",
                ),
                Field("end_date", wrapper_class="flex-1"),
                Field(
                    "max_users",
                    oninput=f"id_total_budget.value = ({self.budget_per_user} + {self.payment_units_max_total}"
                    f"* parseInt(document.getElementById('id_org_pay_per_visit')?.value || 0)) "
                    f"* parseInt(this.value || 0)",
                ),
                Field("total_budget", readonly=True, wrapper_class="form-group "),
                css_class="grid grid-cols-2 gap-6",
            ),
            Row(Submit("submit", "Submit", css_class="button button-md primary-dark"), css_class="flex justify-end"),
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
                    )
                ),
            )
            self.fields["org_pay_per_visit"] = forms.IntegerField(
                required=True, widget=forms.NumberInput(), initial=self.instance.org_pay_per_visit
            )

        self.fields["max_users"] = forms.IntegerField(label="Max Workers", initial=int(self.instance.number_of_users))
        self.fields["start_date"].disabled = self.is_start_date_readonly

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


class DateRanges(TextChoices):
    LAST_7_DAYS = "last_7_days", "Last 7 days"
    LAST_30_DAYS = "last_30_days", "Last 30 days"
    LAST_90_DAYS = "last_90_days", "Last 90 days"
    LAST_YEAR = "last_year", "Last year"
    ALL = "all", "All"

    def get_cutoff_date(self):
        match self:
            case DateRanges.LAST_7_DAYS:
                return now() - relativedelta(days=7)
            case DateRanges.LAST_30_DAYS:
                return now() - relativedelta(days=30)
            case DateRanges.LAST_90_DAYS:
                return now() - relativedelta(days=90)
            case DateRanges.LAST_YEAR:
                return now() - relativedelta(years=1)
            case DateRanges.ALL:
                return None


class VisitExportForm(forms.Form):
    format = forms.ChoiceField(choices=(("csv", "CSV"), ("xlsx", "Excel")), initial="xlsx")
    date_range = forms.ChoiceField(choices=DateRanges.choices, initial=DateRanges.LAST_30_DAYS)
    status = forms.MultipleChoiceField(choices=[("all", "All")] + VisitValidationStatus.choices, initial=["all"])
    flatten_form_data = forms.BooleanField(initial=True, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        self.helper.layout = Layout(
            Row(
                Field("format"),
                Field("date_range"),
                Field("status"),
                Field(
                    "flatten_form_data",
                    css_class=CHECKBOX_CLASS,
                    wrapper_class="flex p-4 justify-between rounded-lg bg-gray-100",
                ),
                css_class="flex flex-col",
            ),
        )
        self.helper.form_tag = False

    def clean_status(self):
        statuses = self.cleaned_data["status"]
        if not statuses or "all" in statuses:
            return []

        return [VisitValidationStatus(status) for status in statuses]


class ReviewVisitExportForm(forms.Form):
    format = forms.ChoiceField(choices=(("csv", "CSV"), ("xlsx", "Excel")), initial="xlsx")
    date_range = forms.ChoiceField(choices=DateRanges.choices, initial=DateRanges.LAST_30_DAYS)
    status = forms.MultipleChoiceField(choices=[("all", "All")] + VisitReviewStatus.choices, initial=["all"])

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        self.helper.layout = Layout(
            Row(
                Field("format"),
                Field("date_range"),
                Field("status"),
                css_class="flex flex-col",
            ),
        )
        self.helper.form_tag = False

    def clean_status(self):
        statuses = self.cleaned_data["status"]
        if not statuses or "all" in statuses:
            return []

        return [VisitReviewStatus(status) for status in statuses]


class PaymentExportForm(forms.Form):
    format = forms.ChoiceField(choices=(("csv", "CSV"), ("xlsx", "Excel")), initial="xlsx")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        self.helper.layout = Layout(
            Row(Field("format"), css_class="flex flex-col"),
        )
        self.helper.form_tag = False


class OpportunityAccessCreationForm(forms.ModelForm):
    user = forms.ModelChoiceField(queryset=User.objects.filter(username__isnull=False))

    class Meta:
        model = OpportunityAccess
        fields = "__all__"


class AddBudgetExistingUsersForm(forms.Form):
    additional_visits = forms.IntegerField(
        widget=forms.NumberInput(attrs={"x-model": "additionalVisits"}), required=False
    )
    end_date = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date", "class": "form-input", "x-model": "end_date"}),
        label="Extended Opportunity End date",
        required=False,
    )

    def __init__(self, *args, **kwargs):
        opportunity_claims = kwargs.pop("opportunity_claims", [])
        self.opportunity = kwargs.pop("opportunity", None)
        super().__init__(*args, **kwargs)

        choices = [(opp_claim.id, opp_claim.id) for opp_claim in opportunity_claims]
        self.fields["selected_users"] = forms.MultipleChoiceField(choices=choices, widget=forms.CheckboxSelectMultiple)

    def clean(self):
        cleaned_data = super().clean()
        selected_users = cleaned_data.get("selected_users")
        additional_visits = cleaned_data.get("additional_visits")

        if not selected_users and not additional_visits and not cleaned_data.get("end_date"):
            raise forms.ValidationError("Please select users and specify either additional visits or end date.")

        if additional_visits and selected_users:
            self.budget_increase = self._validate_budget(selected_users, additional_visits)

        return cleaned_data

    def _validate_budget(self, selected_users, additional_visits):
        claims = OpportunityClaimLimit.objects.filter(opportunity_claim__in=selected_users)
        org_pay = self.opportunity.managedopportunity.org_pay_per_visit if self.opportunity.managed else 0

        budget_increase = sum((ocl.payment_unit.amount + org_pay) * additional_visits for ocl in claims)

        if self.opportunity.managed:
            # NM cannot increase the opportunity budget they can only
            # assign new visits if the opportunity has remaining budget.
            if budget_increase > self.opportunity.remaining_budget:
                raise forms.ValidationError({"additional_visits": "Additional visits exceed the opportunity budget."})

        return budget_increase

    def save(self):
        selected_users = self.cleaned_data["selected_users"]
        additional_visits = self.cleaned_data["additional_visits"]
        end_date = self.cleaned_data["end_date"]

        if additional_visits:
            claims = OpportunityClaimLimit.objects.filter(opportunity_claim__in=selected_users)
            claims.update(max_visits=F("max_visits") + additional_visits)

            if not self.opportunity.managed:
                self.opportunity.total_budget += self.budget_increase
                self.opportunity.save()

        if end_date:
            OpportunityClaim.objects.filter(pk__in=selected_users).update(end_date=end_date)


class AddBudgetNewUsersForm(forms.Form):
    add_users = forms.IntegerField(
        required=False,
        label="Number Of Workers",
        help_text="New Budget Added = Workers Added x Sum of Budget for Each Payment Unit.",
    )
    total_budget = forms.IntegerField(
        required=False,
        label="Opportunity Total Budget",
        help_text="Set a new total budget or leave it unchanged when using Number of workers.",
    )

    def __init__(self, *args, **kwargs):
        self.opportunity = kwargs.pop("opportunity", None)
        self.program_manager = kwargs.pop("program_manager", False)
        super().__init__(*args, **kwargs)

        self.helper = FormHelper(self)
        self.helper.layout = Layout(
            Row(Field("add_users"), Field("total_budget"), css_class="grid grid-cols-2 gap-4"),
            Row(Submit("submit", "Submit", css_class="button button-md primary-dark"), css_class="flex justify-end"),
        )

        self.fields["total_budget"].initial = self.opportunity.total_budget

    def clean(self):
        cleaned_data = super().clean()
        add_users = cleaned_data.get("add_users")
        total_budget = cleaned_data.get("total_budget")

        if self.opportunity.managed and not self.program_manager:
            raise forms.ValidationError("Only program managers are allowed to add budgets for managed opportunities.")

        if not add_users and not total_budget:
            raise forms.ValidationError("Please provide either the number of users or a total budget.")

        if add_users and total_budget and total_budget != self.opportunity.total_budget:
            raise forms.ValidationError(
                "Only one field can be updated at a time: either 'Number of Users' or 'Total Budget'."
            )

        self.budget_increase = self._validate_budget(add_users, total_budget)

        return cleaned_data

    def _validate_budget(self, add_users, total_budget):
        increased_budget = 0
        total_program_budget = 0
        claimed_program_budget = 0
        org_pay = 0

        if self.opportunity.managed:
            manage_opp = self.opportunity.managedopportunity
            org_pay = manage_opp.org_pay_per_visit
            program = manage_opp.program
            total_program_budget = program.budget
            claimed_program_budget = (
                ManagedOpportunity.objects.filter(program=program)
                .exclude(id=manage_opp.id)
                .aggregate(total=Sum("total_budget"))["total"]
                or 0
            )

        if add_users:
            for payment_unit in self.opportunity.paymentunit_set.all():
                increased_budget += (payment_unit.amount + org_pay) * payment_unit.max_total * add_users
            if (
                self.opportunity.managed
                and self.opportunity.total_budget + increased_budget + claimed_program_budget > total_program_budget
            ):
                raise forms.ValidationError({"add_users": "Budget exceeds program budget."})
        else:
            if total_budget < self.opportunity.claimed_budget:
                raise forms.ValidationError({"total_budget": "Total budget cannot be lesser than claimed budget."})

            if self.opportunity.managed and total_budget + claimed_program_budget > total_program_budget:
                raise forms.ValidationError({"total_budget": "Total budget exceeds program budget."})

            increased_budget = total_budget - self.opportunity.total_budget

        return increased_budget

    def save(self):
        self.opportunity.total_budget += self.budget_increase
        self.opportunity.save()


class PaymentUnitForm(forms.ModelForm):
    class Meta:
        model = PaymentUnit
        fields = ["name", "description", "amount", "max_total", "max_daily", "start_date", "end_date"]
        help_texts = {
            "start_date": "Optional. If not specified opportunity start date applies to form submissions.",
            "end_date": "Optional. If not specified opportunity end date applies to form submissions.",
        }
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        deliver_units = kwargs.pop("deliver_units", [])
        payment_units = kwargs.pop("payment_units", [])
        org_slug = kwargs.pop("org_slug")
        opportunity_id = kwargs.pop("opportunity_id")

        super().__init__(*args, **kwargs)

        self.helper = FormHelper(self)
        self.helper.layout = Layout(
            Div(
                Row(
                    Column(Field("name"), Field("description")),
                    Column(
                        Field("amount"),
                        Row(Field("max_total"), Field("max_daily"), css_class="grid grid-cols-2 gap-4"),
                        Field("start_date"),
                        Field("end_date"),
                    ),
                    css_class="grid grid-cols-2 gap-4 p-6 card_bg",
                ),
                Row(
                    Field("required_deliver_units"),
                    Field("payment_units"),
                    Field("optional_deliver_units"),
                    Div(
                        HTML(
                            f"""
                    <button type="button" class="button button-md outline-style" id="sync-button"
                    hx-post="{reverse('opportunity:sync_deliver_units', args=(org_slug, opportunity_id))}"
                    hx-trigger="click" hx-swap="none" hx-on::after-request="alert(event?.detail?.xhr?.response);
                    event.detail.successful && location.reload();
                    this.removeAttribute('disabled'); this.innerHTML='Sync Deliver Units';""
                    hx-disabled-elt="this"
                    hx-on:click="this.innerHTML = 'Syncing...';">
                    <span id="sync-text">Sync Deliver Units</span>
                    </button>

                """
                        )
                    ),
                    css_class="grid grid-cols-2 gap-4 p-6 card_bg",
                ),
                Row(
                    Submit("submit", "Submit", css_class="button button-md primary-dark"), css_class="flex justify-end"
                ),
                css_class="flex flex-col gap-4",
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

        self.helper = FormHelper(self)
        self.helper.layout = Layout(
            Field("selected_users"),
            Field("title"),
            Field("body"),
            Field("message_type"),
            Submit(name="submit", value="Submit"),
        )

        choices = [(user.pk, user.username) for user in users]
        self.fields["selected_users"] = forms.MultipleChoiceField(choices=choices)


class OpportunityVerificationFlagsConfigForm(forms.ModelForm):
    class Meta:
        model = OpportunityVerificationFlags
        fields = ("duplicate", "gps", "location", "form_submission_start", "form_submission_end", "catchment_areas")
        widgets = {
            "form_submission_start": forms.TimeInput(attrs={"type": "time", "class": "form-control"}),
            "form_submission_end": forms.TimeInput(attrs={"type": "time", "class": "form-control"}),
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
            "catchment_areas": "Flag forms outside a users's assigned catchment area",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.helper = FormHelper(self)
        self.helper.form_tag = False

        self.helper.layout = Layout(
            Row(
                Field("duplicate", css_class=f"{CHECKBOX_CLASS} block"),
                Field("gps", css_class=f"{CHECKBOX_CLASS} block"),
                Field("catchment_areas", css_class=f"{CHECKBOX_CLASS} block"),
                css_class="grid grid-cols-3 gap-2",
            ),
            Row(Field("location")),
            Fieldset(
                "Form Submission Hours",
                Row(
                    Field("form_submission_start"),
                    Field("form_submission_end"),
                    css_class="grid grid-cols-2 gap-2",
                ),
            ),
        )

        self.fields["duplicate"].required = False
        self.fields["location"].required = False
        self.fields["gps"].required = False
        self.fields["catchment_areas"].required = False
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

        self.helper = FormHelper(self)
        self.helper.form_tag = False
        self.helper.layout = Layout(
            Row(
                Column(Field("deliver_unit")),
                Column(Field("check_attachments", css_class=CHECKBOX_CLASS)),
                Column(Field("duration")),
                css_class="grid grid-cols-3 gap-2",
            ),
        )
        self.fields["deliver_unit"] = forms.ModelChoiceField(
            queryset=DeliverUnit.objects.filter(app=self.opportunity.deliver_app), disabled=True, empty_label=None
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

    def __init__(self, *args, **kwargs):
        self.opportunity = kwargs.pop("opportunity")
        super().__init__(*args, **kwargs)

        self.helper = FormHelper(self)
        self.helper.form_tag = False
        self.helper.layout = Layout(
            Row(
                Column(Field("name")),
                Column(Field("question_path")),
                Column(Field("question_value")),
                css_class="grid grid-cols-3 gap-2",
            ),
            Field("deliver_unit"),
        )
        self.helper.render_hidden_fields = True

        self.fields["deliver_unit"] = forms.ModelMultipleChoiceField(
            queryset=DeliverUnit.objects.filter(app=self.opportunity.deliver_app),
            widget=forms.CheckboxSelectMultiple,
        )


class PaymentInvoiceForm(forms.ModelForm):
    class Meta:
        model = PaymentInvoice
        fields = ("amount", "date", "invoice_number", "service_delivery")
        widgets = {"date": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *args, **kwargs):
        self.opportunity = kwargs.pop("opportunity")
        super().__init__(*args, **kwargs)

        self.helper = FormHelper(self)
        self.helper.layout = Layout(
            Row(
                Field("amount", min=0),
                Field("date"),
                Field("invoice_number"),
                Field(
                    "service_delivery",
                    css_class=CHECKBOX_CLASS,
                    wrapper_class="flex p-4 justify-between rounded-lg bg-gray-100",
                ),
                css_class="flex flex-col",
            ),
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
