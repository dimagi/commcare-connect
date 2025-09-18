from crispy_forms.helper import FormHelper
from crispy_forms.layout import HTML, Column, Div, Field, Layout
from django import forms
from django.core.exceptions import ValidationError
from django.forms import ModelForm
from django.utils.translation import gettext_lazy as _

from .helpers import process_question_form_data
from .models import Solicitation, SolicitationQuestion, SolicitationResponse, SolicitationReview


class SolicitationResponseForm(forms.Form):
    """
    Dynamic form for responding to solicitations
    Fields are generated based on SolicitationQuestion instances
    """

    # Remove the old single file field - we'll handle multiple files differently

    def __init__(self, solicitation, user, is_draft_save=False, instance=None, *args, **kwargs):
        # Extract instance from kwargs since Form doesn't handle it automatically
        self.instance = instance
        super().__init__(*args, **kwargs)
        self.solicitation = solicitation
        self.user = user
        self.is_draft_save = is_draft_save

        # Dynamically add fields based on solicitation questions
        questions = SolicitationQuestion.objects.filter(solicitation=solicitation).order_by("order")

        for question in questions:
            field_name = f"question_{question.id}"

            # For drafts, never require fields. For submission, use question's requirement
            field_required = question.is_required and not is_draft_save

            if question.question_type == "text":
                field = forms.CharField(
                    label=question.question_text,
                    required=field_required,
                    max_length=500,
                )
            elif question.question_type == "textarea":
                field = forms.CharField(
                    label=question.question_text,
                    required=field_required,
                    widget=forms.Textarea(attrs={"rows": 4}),
                )
            elif question.question_type == "number":
                field = forms.IntegerField(
                    label=question.question_text,
                    required=field_required,
                )
            elif question.question_type == "file":
                # File fields are never required (as per user request)
                field = forms.FileField(
                    label=question.question_text,
                    required=False,
                )
            elif question.question_type == "multiple_choice":
                choices = [("", "Select an option...")]  # Add empty choice for optional fields
                if question.options:
                    for option in question.options:
                        choices.append((option, option))
                field = forms.ChoiceField(
                    label=question.question_text,
                    required=field_required,
                    choices=choices,
                )
            else:
                # Default to text field
                field = forms.CharField(
                    label=question.question_text,
                    required=field_required,
                )

            self.fields[field_name] = field

        # Populate form fields with existing draft data if available
        if self.instance and self.instance.pk and self.instance.responses:
            for question in questions:
                field_name = f"question_{question.id}"
                if field_name in self.fields and question.question_text in self.instance.responses:
                    saved_value = self.instance.responses[question.question_text]
                    # Set initial value regardless of whether it's empty or not
                    self.fields[field_name].initial = saved_value or ""

        # Setup crispy forms
        self.helper = FormHelper(self)
        self.helper.form_class = "space-y-6"
        self.helper.form_method = "post"
        self.helper.form_enctype = "multipart/form-data"
        self.helper.form_tag = False  # Let template handle form tag

        # Create dynamic layout based on questions
        layout_fields = []
        for question in questions:
            field_name = f"question_{question.id}"
            if field_name in self.fields:
                layout_fields.append(
                    Div(Field(field_name, wrapper_class="border border-gray-200 rounded-lg p-6"), css_class="mb-4")
                )

        if layout_fields:
            self.helper.layout = Layout(
                Div(
                    HTML(
                        '<h4 class="text-lg font-medium text-brand-deep-purple mb-4">'
                        '<i class="fa-solid fa-clipboard-question mr-2"></i>'
                        "Application Questions</h4>"
                    ),
                    *layout_fields,
                    css_class="bg-white rounded-lg p-6 space-y-6",
                )
            )

    def full_clean(self):
        """
        Override full_clean to handle draft vs submission validation differently
        """
        # Check if this is a draft save by looking at the POST data
        if hasattr(self, "data") and self.data.get("action") == "save_draft":
            # For draft saves, temporarily make all fields non-required
            original_required = {}
            for field_name, field in self.fields.items():
                if field_name.startswith("question_"):
                    original_required[field_name] = field.required
                    field.required = False

            # Perform validation with non-required fields
            super().full_clean()

            # Restore original required status for future use
            for field_name, was_required in original_required.items():
                if field_name in self.fields:
                    self.fields[field_name].required = was_required
        else:
            # Normal validation for submissions
            super().full_clean()

    def clean(self):
        cleaned_data = super().clean()

        # Validate user has organization membership
        if not self.user.memberships.exists():
            raise ValidationError(_("You must be a member of an organization to submit responses."))

        return cleaned_data

    def save(self, commit=True):
        # Create or update the response instance
        if self.instance:
            response = self.instance
        else:
            response = SolicitationResponse()

        response.solicitation = self.solicitation
        response.submitted_by = self.user

        # Get user's primary organization
        user_org = self.user.memberships.first().organization
        response.organization = user_org

        # Use helper function to process question form data
        responses_data = process_question_form_data(self.cleaned_data, self.is_draft_save)

        # Set the responses data
        response.responses = responses_data

        if commit:
            response.save()

        return response


class SolicitationForm(ModelForm):
    """
    Form for creating and editing solicitations (EOIs/RFPs)
    """

    class Meta:
        model = Solicitation
        fields = [
            "title",
            "description",
            "solicitation_type",
            "expected_start_date",
            "expected_end_date",
            "application_deadline",
            "status",
            "is_publicly_listed",
        ]
        widgets = {
            "title": forms.TextInput(
                attrs={
                    "class": (
                        "w-full px-3 py-2 border border-gray-300 rounded-md "
                        "focus:outline-none focus:ring-2 focus:ring-brand-indigo focus:border-brand-indigo"
                    ),
                    "placeholder": "Enter solicitation title...",
                }
            ),
            "description": forms.Textarea(
                attrs={
                    "class": (
                        "w-full px-3 py-2 border border-gray-300 rounded-md "
                        "focus:outline-none focus:ring-2 focus:ring-brand-indigo focus:border-brand-indigo"
                    ),
                    "rows": 6,
                    "placeholder": "Describe the solicitation, its objectives, and requirements...",
                }
            ),
            "solicitation_type": forms.Select(
                attrs={
                    "class": (
                        "w-full px-3 py-2 border border-gray-300 rounded-md "
                        "focus:outline-none focus:ring-2 focus:ring-brand-indigo focus:border-brand-indigo"
                    )
                }
            ),
            "expected_start_date": forms.DateInput(
                attrs={
                    "class": (
                        "w-full px-3 py-2 border border-gray-300 rounded-md "
                        "focus:outline-none focus:ring-2 focus:ring-brand-indigo focus:border-brand-indigo"
                    ),
                    "type": "date",
                }
            ),
            "expected_end_date": forms.DateInput(
                attrs={
                    "class": (
                        "w-full px-3 py-2 border border-gray-300 rounded-md "
                        "focus:outline-none focus:ring-2 focus:ring-brand-indigo focus:border-brand-indigo"
                    ),
                    "type": "date",
                }
            ),
            "application_deadline": forms.DateInput(
                attrs={
                    "class": (
                        "w-full px-3 py-2 border border-gray-300 rounded-md "
                        "focus:outline-none focus:ring-2 focus:ring-brand-indigo focus:border-brand-indigo"
                    ),
                    "type": "date",
                }
            ),
            "status": forms.Select(
                attrs={
                    "class": (
                        "w-full px-3 py-2 border border-gray-300 rounded-md "
                        "focus:outline-none focus:ring-2 focus:ring-brand-indigo focus:border-brand-indigo"
                    )
                }
            ),
            "is_publicly_listed": forms.CheckboxInput(attrs={"class": "simple-checkbox"}),
        }

    def __init__(self, program=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.program = program

        # Add help text (labels are now automatically populated from model verbose_name)
        self.fields["description"].help_text = "Provide a detailed description of the solicitation"
        self.fields["is_publicly_listed"].help_text = "Check to make this solicitation visible in public listings"

        # Setup crispy forms
        self.helper = FormHelper(self)
        self.helper.form_class = "space-y-8"
        self.helper.form_id = "solicitation-form"
        self.helper.form_method = "post"

        self.helper.layout = Layout(
            # Hidden field for questions data
            HTML('<input type="hidden" name="questions_data" id="questions-data" value="">'),
            # Basic Information Section
            Div(
                HTML(
                    '<h2 class="text-xl font-semibold text-brand-deep-purple mb-6 pb-2 border-b border-gray-200">'
                    '<i class="fa-solid fa-info-circle mr-2"></i>Basic Information</h2>'
                ),
                Div(
                    # Title (full width)
                    Field("title", wrapper_class="mb-6"),
                    # Row 2: Type, Status, and Visibility
                    Div(
                        Column(Field("solicitation_type"), css_class="flex-1"),
                        Column(Field("status"), css_class="flex-1"),
                        Column(Field("is_publicly_listed"), css_class="flex-1"),
                        css_class="flex flex-col md:flex-row gap-6 mb-6",
                    ),
                    # Description
                    Field("description", wrapper_class="mb-6"),
                    # Timeline Fields Row
                    Div(
                        Column(Field("application_deadline"), css_class="flex-1"),
                        Column(Field("expected_start_date"), css_class="flex-1"),
                        Column(Field("expected_end_date"), css_class="flex-1"),
                        css_class="flex flex-col md:flex-row gap-4 mb-6",
                    ),
                    css_class="space-y-6",
                ),
                css_class="bg-white rounded-xl shadow-sm p-8 mb-8",
            ),
        )

        # Don't render submit buttons - let template handle them
        self.helper.form_tag = False

    def clean(self):
        cleaned_data = super().clean()

        # Validate date ranges
        start_date = cleaned_data.get("expected_start_date")
        end_date = cleaned_data.get("expected_end_date")
        deadline = cleaned_data.get("application_deadline")

        if start_date and end_date and start_date >= end_date:
            raise ValidationError("Expected end date must be after the start date.")

        if deadline and start_date and deadline >= start_date:
            raise ValidationError("Application deadline must be before the expected start date.")

        return cleaned_data

    def save(self, commit=True):
        solicitation = super().save(commit=False)

        if self.program:
            solicitation.program = self.program

        # Set default values for fields we're no longer collecting in the form
        if not solicitation.target_population:
            solicitation.target_population = "To be determined"
        if not solicitation.scope_of_work:
            solicitation.scope_of_work = "Details will be provided through application questions"
        if not solicitation.estimated_scale:
            solicitation.estimated_scale = "To be determined"

        if commit:
            solicitation.save()

        return solicitation


class SolicitationReviewForm(forms.ModelForm):
    """
    Form for reviewing solicitation responses
    """

    class Meta:
        model = SolicitationReview
        fields = ["score", "notes", "tags", "recommendation"]

    score = forms.IntegerField(
        label="Score (1-100)",
        min_value=1,
        max_value=100,
        required=False,
        widget=forms.NumberInput(attrs={"placeholder": "Enter score 1-100"}),
    )
    notes = forms.CharField(
        label="Review Notes",
        widget=forms.Textarea(attrs={"rows": 4, "placeholder": "Add your review notes..."}),
        required=False,
        help_text="Provide detailed feedback about this response",
    )
    tags = forms.CharField(
        label="Tags",
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "e.g., strong-technical, needs-clarification"}),
        help_text="Add comma-separated tags to categorize this response",
    )
    recommendation = forms.ChoiceField(
        label="Recommendation",
        choices=SolicitationReview.Recommendation.choices,
        required=False,
        help_text="Your overall recommendation for this response",
    )

    def __init__(self, response=None, reviewer=None, *args, **kwargs):
        # Store context for potential use
        self.response = response
        self.reviewer = reviewer
        super().__init__(*args, **kwargs)

        # Pre-populate fields if editing existing review
        if self.instance:
            self.fields["score"].initial = self.instance.score
            self.fields["notes"].initial = self.instance.notes
            self.fields["tags"].initial = self.instance.tags
            self.fields["recommendation"].initial = self.instance.recommendation

        # Setup crispy forms
        self.helper = FormHelper(self)
        self.helper.form_method = "post"
        self.helper.form_tag = False  # Let template handle form tag
        self.helper.form_class = "space-y-6"

        self.helper.layout = Layout(
            Div(
                HTML('<h2 class="text-xl font-semibold text-brand-deep-purple mb-6">' "Your Review</h2>"),
                # First row - Review Notes (full width)
                Field("notes", wrapper_class="mb-6"),
                # Second row - Score, Recommendation, and Tags (three columns)
                Div(
                    Column(Field("score"), css_class="flex-1"),
                    Column(Field("recommendation"), css_class="flex-1"),
                    Column(Field("tags"), css_class="flex-1"),
                    css_class="flex flex-col md:flex-row gap-4 mb-6",
                ),
                css_class="bg-white rounded-xl shadow-sm p-8",
            )
        )
