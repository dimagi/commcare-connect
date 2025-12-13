from crispy_forms.helper import FormHelper
from crispy_forms.layout import Field, Layout, Submit
from django import forms
from django.core.exceptions import ValidationError
from django.db import models as django_models

from commcare_connect.opportunity.models import Opportunity, OpportunityAccess
from commcare_connect.tasks.models import Task, TaskComment
from commcare_connect.users.models import User


class TaskCreateForm(forms.ModelForm):
    """Form for creating a new task."""

    class Meta:
        model = Task
        fields = [
            "user",
            "opportunity",
            "task_type",
            "priority",
            "title",
            "description",
            "learning_assignment_text",
            "assigned_to",
            "audit_session_id",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
            "learning_assignment_text": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        self.request_user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)

        # Limit opportunities to ones the current user can access
        if self.request_user and not self.request_user.is_superuser:
            accessible_opps = Opportunity.objects.filter(
                django_models.Q(opportunityaccess__user=self.request_user)
                | django_models.Q(organization__memberships__user=self.request_user)
            ).distinct()
            self.fields["opportunity"].queryset = accessible_opps

        self.helper = FormHelper(self)
        self.helper.layout = Layout(
            Field("user"),
            Field("opportunity"),
            Field("task_type"),
            Field("priority"),
            Field("title"),
            Field("description"),
            Field("learning_assignment_text"),
            Field("assigned_to"),
            Field("audit_session_id"),
            Submit("submit", "Create Task", css_class="button button-md primary-dark float-end"),
        )
        self.helper.form_tag = False

    def clean(self):
        cleaned_data = super().clean()
        user = cleaned_data.get("user")
        opportunity = cleaned_data.get("opportunity")

        if user and opportunity:
            # Verify the user has access to the opportunity
            if not OpportunityAccess.objects.filter(user=user, opportunity=opportunity).exists():
                raise ValidationError("The selected user does not have access to the selected opportunity.")

        return cleaned_data


class TaskUpdateForm(forms.ModelForm):
    """Form for updating an existing task."""

    class Meta:
        model = Task
        fields = [
            "status",
            "priority",
            "assigned_to",
            "learning_assignment_text",
        ]
        widgets = {
            "learning_assignment_text": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.helper = FormHelper(self)
        self.helper.layout = Layout(
            Field("status"),
            Field("priority"),
            Field("assigned_to"),
            Field("learning_assignment_text"),
            Submit("submit", "Update Task", css_class="button button-md primary-dark float-end"),
        )
        self.helper.form_tag = False


class TaskCommentForm(forms.ModelForm):
    """Form for adding comments to a task."""

    class Meta:
        model = TaskComment
        fields = ["content"]
        widgets = {
            "content": forms.Textarea(attrs={"rows": 3, "placeholder": "Add a comment..."}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.helper = FormHelper(self)
        self.helper.layout = Layout(
            Field("content"),
            Submit("submit", "Add Comment", css_class="button button-md primary-dark"),
        )
        self.helper.form_tag = False


class TaskQuickUpdateForm(forms.Form):
    """Lightweight form for AJAX status/assignment updates."""

    status = forms.ChoiceField(
        choices=Task._meta.get_field("status").choices,
        required=False,
    )
    assigned_to = forms.ModelChoiceField(
        queryset=User.objects.all(),
        required=False,
    )
    priority = forms.ChoiceField(
        choices=Task._meta.get_field("priority").choices,
        required=False,
    )
