from crispy_forms.helper import FormHelper
from crispy_forms.layout import Field, Layout
from django import forms
from django.utils.translation import gettext_lazy as _

from commcare_connect.microplanning.models import WorkArea, WorkAreaGroup
from commcare_connect.opportunity.models import OpportunityAccess

INPUT_CSS = (
    "w-full rounded-md border border-gray-300 px-3 py-2 "
    "text-sm shadow-sm focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500"
)


class WorkAreaModelForm(forms.ModelForm):
    reason = forms.CharField(
        required=False,
        label=_("Reason for change (Optional)"),
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": _("Enter reason...")}),
    )

    class Meta:
        model = WorkArea
        fields = ["expected_visit_count", "work_area_group"]
        widgets = {
            "work_area_group": forms.Select(attrs={"data-tomselect": "1", "data-tomselect:no-remove-button": "1"})
        }
        labels = {
            "expected_visit_count": _("Expected Visit Count"),
            "work_area_group": _("Work Area Group"),
        }

    def __init__(self, *args, **kwargs):
        opportunity = kwargs.pop("opportunity", None)
        super().__init__(*args, **kwargs)
        if opportunity:
            self.fields["work_area_group"].queryset = WorkAreaGroup.objects.filter(opportunity=opportunity)
        self.helper = FormHelper(self)
        self.helper.form_tag = False
        self.helper.layout = Layout(
            Field("expected_visit_count"),
            Field("work_area_group"),
            Field("reason"),
        )

    def has_changed(self):
        # Ignore "reason" form is unchanged unless model fields change
        return any(f in self.changed_data for f in self._meta.fields)


class AssignmentModeForm(forms.Form):
    work_area_group = forms.ModelChoiceField(
        queryset=WorkAreaGroup.objects.none(),
        required=False,
        empty_label=_("— Select Group —"),
        label=_("Select Work Area Group"),
        widget=forms.Select(
            attrs={
                "class": INPUT_CSS,
                "x-ref": "groupSelect",
                "@change": "selectGroup($event.target.value)",
            }
        ),
    )

    assignee = forms.ModelChoiceField(
        queryset=OpportunityAccess.objects.none(),
        required=False,
        empty_label=_("— Select FLW —"),
        label=_("Select new Assignee"),
        widget=forms.Select(
            attrs={
                "class": INPUT_CSS,
                "x-ref": "assigneeSelect",
                "@change": "selectedAssigneeId = $event.target.value; selectByAssignee($event.target.value)",
            }
        ),
    )

    def __init__(self, *args, opportunity=None, **kwargs):
        super().__init__(*args, **kwargs)
        if opportunity:
            self.fields["work_area_group"].queryset = WorkAreaGroup.objects.filter(opportunity=opportunity)
            self.fields["assignee"].queryset = (
                OpportunityAccess.objects.filter(opportunity=opportunity, accepted=True, suspended=False)
                .select_related("user")
                .order_by("user__name")
            )
            self.fields["assignee"].label_from_instance = lambda obj: obj.user.name
