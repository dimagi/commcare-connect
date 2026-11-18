"""Assigned-task forms: creating and editing tasks and task types."""

import datetime
import json
import logging
from urllib.parse import urlencode

import httpx
from crispy_forms.helper import FormHelper
from crispy_forms.layout import HTML, Div, Field, Layout
from django import forms
from django.core.exceptions import ValidationError
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.timezone import now
from django.utils.translation import gettext_lazy as _

from commcare_connect.opportunity.app_xml import get_task_units_for_app
from commcare_connect.opportunity.models import (
    AssignedTask,
    AssignedTaskStatus,
    OpportunityAccess,
    TaskType,
    TaskTypeModeChoices,
)
from commcare_connect.utils.commcarehq_api import CommCareHQAPIException
from commcare_connect.utils.ocs_api import user_has_connected_ocs

logger = logging.getLogger(__name__)


class CreateTaskForm(forms.Form):
    task = forms.ModelChoiceField(
        label=_("Task"),
        queryset=TaskType.objects.none(),
        empty_label=_("Select a task"),
        widget=forms.Select(
            attrs={
                "data-tomselect": "1",
                "@change": "selectedTaskIsOcs = ocsTaskIds.includes($event.target.value)",
            }
        ),
    )
    access = forms.ModelChoiceField(
        label=_("Connect Worker"),
        queryset=OpportunityAccess.objects.none(),
        empty_label=_("Select a Connect Worker"),
        widget=forms.Select(attrs={"data-tomselect": "1"}),
    )
    due_date = forms.DateField(
        label=_("Due Date"),
        widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
    )

    def __init__(self, *args, opportunity=None, access=None, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.ocs_connected = user_has_connected_ocs(user)
        self.ocs_task_ids_json = "[]"
        ocs_task_id_strings = []
        if opportunity is not None:
            task_qs = TaskType.objects.filter(app=opportunity.deliver_app, is_active=True)
            if access is not None:
                already_assigned = AssignedTask.objects.filter(
                    opportunity_access=access,
                    status=AssignedTaskStatus.ASSIGNED,
                ).values_list("task_type_id", flat=True)
                task_qs = task_qs.exclude(pk__in=already_assigned)
            self.fields["task"].queryset = task_qs
            self.fields["access"].queryset = OpportunityAccess.objects.filter(
                opportunity=opportunity,
                accepted=True,
                suspended=False,
            ).select_related("user")
            self.fields["access"].label_from_instance = lambda obj: obj.user.display_name_with_username()

            ocs_task_pks = task_qs.filter(mode=TaskTypeModeChoices.OCS).values_list("pk", flat=True)
            # Option <select> values are strings, so stringify the pks to match.
            ocs_task_id_strings = [str(pk) for pk in ocs_task_pks]
            self.ocs_task_ids_json = json.dumps(ocs_task_id_strings)

        selected_task_id = self.data.get("task")
        self.selected_task_is_ocs = self.is_bound and str(selected_task_id) in ocs_task_id_strings

        if access is not None:
            self.fields["access"].initial = access.pk
            self.fields["access"].widget = forms.HiddenInput()

        self.fields["due_date"].widget.attrs["min"] = datetime.date.today().isoformat()

        self.helper = FormHelper(self)
        self.helper.form_tag = False
        self.helper.layout = Layout(
            Field("task"),
            Field("access"),
            Field("due_date"),
        )

    def clean_due_date(self):
        due_date = self.cleaned_data["due_date"]
        if due_date < datetime.date.today():
            raise ValidationError(_("Due date cannot be in the past."))
        return due_date

    def clean(self):
        cleaned_data = super().clean()
        task = cleaned_data.get("task")
        if task and task.mode == TaskTypeModeChoices.OCS and not self.ocs_connected:
            raise ValidationError(_("Connect your Open Chat Studio account to assign OCS tasks."))
        return cleaned_data


class AddTaskTypeForm(forms.ModelForm):
    task_unit_id = forms.ChoiceField(
        label=_("Task unit"),
        choices=[],
        required=False,
        widget=forms.Select(attrs={"@change": "onTaskUnitSelectChange($event.target.value)"}),
    )
    mode = forms.ChoiceField(
        label=_("Task mode"),
        choices=TaskTypeModeChoices.choices,
        initial=TaskTypeModeChoices.RELEARN,
        required=False,
        widget=forms.Select(attrs={"x-model": "taskMode"}),
    )

    class Meta:
        model = TaskType
        fields = ["mode", "name", "description", "case_property", "ocs_chatbot_id"]
        widgets = {"description": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, opportunity, org_slug=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.opportunity = opportunity
        self._populate_task_unit_choices()

        org_slug = org_slug or opportunity.organization.slug
        ocs_section_url = reverse(
            "opportunity:task_type_ocs_section",
            args=(org_slug, opportunity.opportunity_id),
        )
        # Preserve the chosen chatbot across a validation-error round-trip: the
        # OCS section is (re)loaded fresh via htmx, so pass the submitted value
        # through so the endpoint can re-select it.
        selected_chatbot_id = self.data.get("ocs_chatbot_id")
        if selected_chatbot_id:
            ocs_section_url = f"{ocs_section_url}?{urlencode({'selected': selected_chatbot_id})}"

        self.helper = FormHelper(self)
        self.helper.form_tag = False
        self.helper.layout = Layout(
            Field("mode"),
            Div(
                Field("task_unit_id"),
                Field("case_property"),
                **{"x-show": "taskMode === 'relearn'"},
            ),
            Div(
                Div(
                    HTML(render_to_string("opportunity/_ocs_loading_spinner.html")),
                    id="ocs-task-section",
                    **{
                        "hx-get": ocs_section_url,
                        "hx-trigger": "intersect once",
                        "hx-target": "#ocs-task-section",
                        "hx-swap": "innerHTML",
                    },
                ),
                css_class="my-4",
                **{"x-show": "taskMode === 'ocs'"},
            ),
            Field("name"),
            Field("description"),
        )

    def _populate_task_unit_choices(self):
        try:
            task_units = get_task_units_for_app(self.opportunity.deliver_app)
        except (httpx.TimeoutException, httpx.ConnectError, CommCareHQAPIException):
            logger.exception("Failed to fetch task units for app %s", self.opportunity.deliver_app.pk)
            self.fields["task_unit_id"].choices = [("", _("Failed to load task units"))]
            self.fields["task_unit_id"].widget.attrs["disabled"] = True
            self.task_units_data = json.dumps({})
            return
        already_used_slugs = set(
            TaskType.objects.filter(app=self.opportunity.deliver_app).values_list("slug", flat=True)
        )
        available_units = [tu for tu in task_units if tu.id not in already_used_slugs]
        if available_units:
            self.fields["task_unit_id"].choices = [("", _("Select a task unit"))] + [
                (tu.id, tu.name) for tu in available_units
            ]
        else:
            self.fields["task_unit_id"].choices = [("", _("No available task units"))]
            self.fields["task_unit_id"].widget.attrs["disabled"] = True
        self.task_units_data = json.dumps(
            {tu.id: {"name": tu.name, "description": tu.description} for tu in available_units}
        )

    def clean(self):
        cleaned_data = super().clean()
        mode = cleaned_data.get("mode") or TaskTypeModeChoices.RELEARN
        cleaned_data["mode"] = mode
        if mode == TaskTypeModeChoices.OCS:
            self._clean_ocs(cleaned_data)
        else:
            self._clean_relearn(cleaned_data)
        return cleaned_data

    def _clean_ocs(self, cleaned_data):
        chatbot_id = cleaned_data.get("ocs_chatbot_id")
        if not chatbot_id:
            self.add_error(None, _("Please select a chatbot."))
        elif self._slug_exists(chatbot_id):
            self.add_error(None, _("A task type for this chatbot already exists."))

    def _clean_relearn(self, cleaned_data):
        task_unit_id = cleaned_data.get("task_unit_id")
        if not task_unit_id:
            self.add_error("task_unit_id", _("Please select a task unit."))
        elif self._slug_exists(task_unit_id):
            self.add_error("task_unit_id", _("A task type with this task unit ID already exists."))

    def _slug_exists(self, slug):
        return TaskType.objects.filter(app=self.opportunity.deliver_app, slug=slug).exists()

    def save(self, commit=True):
        task_type = super().save(commit=False)
        task_type.app = self.opportunity.deliver_app
        task_type.opportunity = self.opportunity
        if self.cleaned_data["mode"] == TaskTypeModeChoices.OCS:
            task_type.slug = self.cleaned_data["ocs_chatbot_id"]
            task_type.case_property = None
            task_type.unit_name = ""
        else:
            task_type.slug = self.cleaned_data["task_unit_id"]
            unit_name = dict(self.fields["task_unit_id"].choices).get(task_type.slug, "")
            task_type.unit_name = unit_name[:255]
            task_type.ocs_chatbot_id = None
        if commit:
            task_type.save()
        return task_type


class EditTaskTypeForm(forms.ModelForm):
    is_archived = forms.BooleanField(required=False, label=_("Archive this task type"))

    class Meta:
        model = TaskType
        fields = ["name", "case_property", "description"]
        widgets = {"description": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.archived:
            self.fields["is_archived"].initial = True
        if self._has_assigned_tasks():
            self._lock_case_property()
        self.helper = FormHelper(self)
        self.helper.form_tag = False
        self.helper.layout = Layout(
            Field("name"),
            Field("case_property"),
            Field("description"),
            Field("is_archived"),
        )

    def _has_assigned_tasks(self):
        return bool(self.instance.pk) and AssignedTask.objects.filter(task_type=self.instance).exists()

    def _lock_case_property(self):
        field = self.fields["case_property"]
        field.disabled = True
        field.help_text = _(
            "This cannot be changed because the task type has already been assigned to one or more workers."
        )

    def save(self, commit=True):
        instance = super().save(commit=False)
        if self.cleaned_data["is_archived"]:
            if not instance.archived:
                instance.archived = now()
            instance.is_active = False
        else:
            instance.archived = None
            instance.is_active = True
        if commit:
            instance.save()
        return instance


class EditAssignedTaskForm(forms.ModelForm):
    reason = forms.CharField(
        required=False,
        label=_("Reason for change (Optional)"),
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": _("Enter reason...")}),
    )

    class Meta:
        model = AssignedTask
        fields = ["due_date"]
        widgets = {
            "due_date": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["due_date"].label = _("New End Date")
        self.fields["due_date"].widget.attrs["min"] = datetime.date.today().isoformat()
        self.helper = FormHelper(self)
        self.helper.form_tag = False

    def clean_due_date(self):
        due_date = self.cleaned_data["due_date"]
        if due_date < datetime.date.today():
            raise ValidationError(_("Due date cannot be in the past."))
        return due_date

    def has_changed(self):
        # Ignore "reason" field if no updated due date is given
        return "due_date" in self.changed_data
