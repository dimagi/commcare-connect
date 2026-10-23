from operator import attrgetter

from crispy_forms.helper import FormHelper
from crispy_forms.layout import Field, Layout, Row
from django import forms
from django.contrib.postgres.forms import SimpleArrayField
from django.forms import BaseInlineFormSet, inlineformset_factory
from django.utils.translation import gettext_lazy as _

from commcare_connect.opportunity.forms import CHECKBOX_CLASS
from commcare_connect.opportunity.models import Country, Currency
from commcare_connect.program.forms import DATE_INPUT
from commcare_connect.program.models import Program
from commcare_connect.solicitation.models import Solicitation, SolicitationEvaluationCriterion, SolicitationQuestion

DATETIME_INPUT = forms.DateTimeInput(format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local"})
TOGGLE_WRAPPER_CLASS = "bg-slate-100 flex items-center justify-between p-4 rounded-lg"
SEARCHABLE_SELECT = forms.Select(attrs={"data-tomselect": "1", "data-tomselect:no-remove-button": "1"})

# A draft may leave these empty; publishing needs them, so an active solicitation keeps them required.
PUBLISH_REQUIRED_FIELDS = ("description", "contact_email", "country")


class HiddenDeleteInlineFormSet(BaseInlineFormSet):
    # A row's remove button sets Alpine's `removed`, which marks the row for deletion here.
    deletion_widget = forms.HiddenInput(attrs={":value": "removed ? 'on' : ''"})


class BaseQuestionInlineFormSet(HiddenDeleteInlineFormSet):
    def get_form_kwargs(self, index):
        return {"criteria": self.instance.criteria.all()}


class SolicitationForm(forms.ModelForm):
    currency = forms.ModelChoiceField(
        label=_("Currency"),
        queryset=Currency.objects.order_by("code"),
        empty_label=_("Select a currency"),
        widget=SEARCHABLE_SELECT,
    )
    country = forms.ModelChoiceField(
        label=_("Country"),
        queryset=Country.objects.order_by("name"),
        empty_label=_("Select a country"),
        widget=SEARCHABLE_SELECT,
    )

    class Meta:
        model = Solicitation
        fields = [
            "title",
            "type",
            "description",
            "budget_min",
            "budget_max",
            "currency",
            "country",
            "delivery_type",
            "expected_start_date",
            "expected_end_date",
            "application_deadline",
            "estimated_scale",
            "contact_email",
            "program",
            "public",
            "hide_scores_until_submit",
        ]
        labels = {
            "title": _("Title"),
            "type": _("Type"),
            "description": _("Scope of work"),
            "budget_min": _("Minimum budget"),
            "budget_max": _("Maximum budget"),
            "delivery_type": _("Delivery type"),
            "expected_start_date": _("Expected start date"),
            "expected_end_date": _("Expected end date"),
            "application_deadline": _("Application deadline"),
            "estimated_scale": _("Estimated scale"),
            "contact_email": _("Contact email"),
            "program": _("Program"),
            "public": _("Public (anyone can see and apply)"),
            "hide_scores_until_submit": _("Hide scores until submit"),
        }
        widgets = {
            "expected_start_date": DATE_INPUT,
            "expected_end_date": DATE_INPUT,
            "application_deadline": DATETIME_INPUT,
            "program": SEARCHABLE_SELECT,
            "delivery_type": SEARCHABLE_SELECT,
        }

    def __init__(self, *args, organization, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.organization = organization
        self.user = user
        self.fields["program"].queryset = Program.objects.filter(organization=organization).order_by("name")
        # Program.__str__ is its slug; PMs pick programs by name.
        self.fields["program"].label_from_instance = attrgetter("name")
        if self.instance.status == Solicitation.Status.DRAFT:
            for name in PUBLISH_REQUIRED_FIELDS:
                self.fields[name].required = False
        self.helper = FormHelper()
        self.helper.form_tag = False
        self.helper.layout = Layout(
            Row("title", "type", css_class="grid grid-cols-2 gap-2"),
            "description",
            Row("budget_min", "budget_max", "currency", css_class="grid grid-cols-3 gap-2"),
            Row("country", "delivery_type", "estimated_scale", css_class="grid grid-cols-3 gap-2"),
            Row(
                "expected_start_date", "expected_end_date", "application_deadline", css_class="grid grid-cols-3 gap-2"
            ),
            Row("contact_email", "program", css_class="grid grid-cols-2 gap-2"),
            Row(
                Field("public", css_class=CHECKBOX_CLASS, wrapper_class=TOGGLE_WRAPPER_CLASS),
                Field("hide_scores_until_submit", css_class=CHECKBOX_CLASS, wrapper_class=TOGGLE_WRAPPER_CLASS),
                css_class="grid grid-cols-2 gap-2",
            ),
        )

    def clean(self):
        cleaned_data = super().clean()
        self._validate_budget(cleaned_data)
        self._validate_dates(cleaned_data)
        self._validate_deadline(cleaned_data)
        return cleaned_data

    def _validate_budget(self, cleaned_data):
        budget_min = cleaned_data.get("budget_min")
        budget_max = cleaned_data.get("budget_max")
        if budget_min is not None and budget_max is not None and budget_max < budget_min:
            self.add_error("budget_max", _("Maximum budget must not be less than the minimum."))

    def _validate_dates(self, cleaned_data):
        start_date = cleaned_data.get("expected_start_date")
        end_date = cleaned_data.get("expected_end_date")
        if start_date and end_date and end_date <= start_date:
            self.add_error("expected_end_date", _("End date must be after the start date."))

    def _validate_deadline(self, cleaned_data):
        deadline = cleaned_data.get("application_deadline")
        if not deadline or self.instance.status != Solicitation.Status.ACTIVE:
            return
        # clean() runs before the instance is updated, so it still holds the stored deadline. The input
        # only has minute precision, so seconds are dropped to let an unchanged deadline through.
        if deadline < self.instance.application_deadline.replace(second=0, microsecond=0):
            self.add_error("application_deadline", _("A published deadline can only be extended."))

    def save(self, commit=True):
        if not self.instance.pk:
            self.instance.organization = self.organization
            self.instance.created_by = self.user.email
        self.instance.modified_by = self.user.email
        return super().save(commit=commit)


class FormsetRowForm(forms.ModelForm):
    """A formset row rendered with {% crispy %} inside the page's form.

    The formset adds the id and DELETE fields after __init__, so they're kept as unmentioned hidden fields.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        self.helper.form_tag = False
        self.helper.render_hidden_fields = True


class CriterionForm(FormsetRowForm):
    # Bounded so out-of-range weights can't offset each other into a 100% total.
    weight = forms.DecimalField(label=_("Weight (%)"), max_digits=5, decimal_places=2, min_value=0, max_value=100)

    class Meta:
        model = SolicitationEvaluationCriterion
        fields = ["label", "weight", "description"]
        labels = {"label": _("Label"), "description": _("Reviewer guidance")}
        widgets = {"description": forms.Textarea(attrs={"rows": 4})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper["description"].wrap(Field, wrapper_class="col-span-2")


class QuestionForm(FormsetRowForm):
    choice_options = SimpleArrayField(
        forms.CharField(),
        label=_("Choice options"),
        delimiter="\n",
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
        help_text=_("One option per line. Only used by choice questions."),
    )

    class Meta:
        model = SolicitationQuestion
        fields = ["text", "help_text", "question_type", "choice_options", "required", "criterion"]
        labels = {
            "text": _("Text"),
            "help_text": _("Help text"),
            "question_type": _("Question type"),
            "required": _("Required"),
            "criterion": _("Criterion"),
        }
        widgets = {
            "text": forms.Textarea(attrs={"rows": 2}),
            "help_text": forms.Textarea(attrs={"rows": 2}),
            "criterion": SEARCHABLE_SELECT,
        }

    def __init__(self, *args, criteria, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["criterion"].queryset = criteria
        self.helper["required"].wrap(Field, css_class=CHECKBOX_CLASS, wrapper_class=TOGGLE_WRAPPER_CLASS)

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("question_type") == SolicitationQuestion.QuestionType.CHOICE:
            if not cleaned_data.get("choice_options"):
                self.add_error("choice_options", _("Choice questions need at least one option."))
        else:
            cleaned_data["choice_options"] = None
        return cleaned_data


CriterionInlineFormSet = inlineformset_factory(
    Solicitation, SolicitationEvaluationCriterion, form=CriterionForm, formset=HiddenDeleteInlineFormSet, extra=0
)
QuestionInlineFormSet = inlineformset_factory(
    Solicitation, SolicitationQuestion, form=QuestionForm, formset=BaseQuestionInlineFormSet, extra=0
)
