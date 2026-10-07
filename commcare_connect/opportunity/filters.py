from datetime import date

import django_filters
from crispy_forms.helper import FormHelper
from crispy_forms.layout import HTML, Column, Div, Field, Layout, Row
from django import forms
from django.utils.html import format_html
from django.utils.timezone import now
from django.utils.translation import gettext_lazy as _

from commcare_connect.opportunity.models import (
    AssignedTaskStatus,
    DeliveryType,
    OpportunityAccess,
    OpportunityStatus,
    TaskType,
)
from commcare_connect.program.utils import opportunities_accessible_to_org, programs_accessible_to_org
from commcare_connect.users.models import User
from commcare_connect.utils.datetime import DateRanges


class FilterMixin:
    """
    Usage:
        - Define a filter using django_filters.FilterSet
        - Mixin this on a view and set filter_class to the above filter
        - Use get_filter_form() to use it in the template
        - Use get_filter_values() to get filter values in the template
    """

    filter_class = None

    def _get_filter_class(self):
        return self.filter_class

    def get_filter_kwargs(self):
        """
        Override this in subclasses to pass extra kwargs to the filterset.
        Always include `queryset`.
        """
        return {
            "queryset": OpportunityAccess.objects.none(),
            "request": self.request,
        }

    def _get_filter(self):
        if not hasattr(self, "_filter_instance"):
            filter_class = self._get_filter_class()
            if filter_class:
                self._filter_instance = filter_class(self.request.GET, **self.get_filter_kwargs())
            else:
                self._filter_instance = None
        return self._filter_instance

    def get_filter_form(self):
        f = self._get_filter()
        if f:
            return f.form
        return None

    def get_filter_values(self):
        f = self._get_filter()
        if f and f.form.is_valid():
            return {name: f.form.cleaned_data.get(name) for name in f.filters.keys()}
        return {}

    def get_applied_filters(self):
        return [name for name, value in self.get_filter_values().items() if value not in (None, "", [])]

    def get_filter_usage_data(self):
        applied = self.get_applied_filters()
        if not applied:
            return None

        return {
            "filters": applied,
            "filter_count": len(applied),
            "page_path": self.request.path,
        }

    def filters_applied_count(self):
        return len(self.get_applied_filters())

    def get_filter_context(self):
        return {
            "filter_form": self.get_filter_form(),
            "filters_applied_count": self.filters_applied_count(),
            "filter_usage_data": self.get_filter_usage_data(),
        }


class CSRFExemptForm(forms.Form):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.disable_csrf = True
        self.helper.form_tag = False


class YesNoFilter(django_filters.BooleanFilter):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault(
            "widget",
            forms.Select(
                choices=[
                    ("", "---------"),
                    (True, "Yes"),
                    (False, "No"),
                ]
            ),
        )
        super().__init__(*args, **kwargs)


class DeliverFilterSet(django_filters.FilterSet):
    last_active = django_filters.ChoiceFilter(
        label="Last Active",
        choices=(
            (1, "1 day ago"),
            (3, "3 days ago"),
            (7, "7 days ago"),
        ),
        empty_label="Any time",
    )
    has_duplicates = YesNoFilter(
        label="Has Duplicate Deliveries",
    )
    has_flags = YesNoFilter(
        label="Deliveries with flags",
    )
    has_overlimit = YesNoFilter(
        label="Has Overlimit Deliveries",
    )
    review_pending = YesNoFilter(
        label="Deliveries with Pending Review",
    )

    class Meta:
        form = CSRFExemptForm

    def __init__(self, *args, **kwargs):
        opportunity = kwargs.pop("opportunity")
        super().__init__(*args, **kwargs)
        if opportunity.automatic_visit_verification:
            self.filters.pop("review_pending")
            self.filters.pop("has_duplicates")


_PAST_PRESETS = [
    DateRanges.LAST_30_DAYS,
    DateRanges.LAST_3_MONTHS,
    DateRanges.LAST_6_MONTHS,
    DateRanges.LAST_12_MONTHS,
]
_FUTURE_PRESETS = [DateRanges.NEXT_30_DAYS, DateRanges.NEXT_3_MONTHS, DateRanges.NEXT_6_MONTHS]


def _preset_choices(*presets):
    return [(preset.value, preset.label) for preset in presets]


# (range dropdown, from date, to date) for each date the opportunity list filters on
OPPORTUNITY_LIST_DATE_RANGES = (
    ("start_date_range", "start_date_from", "start_date_to"),
    ("end_date_range", "end_date_from", "end_date_to"),
)
# Filled in from their range dropdown, which is the filter the user applied
DATE_RANGE_BOUND_FILTERS = {name for _range, *bounds in OPPORTUNITY_LIST_DATE_RANGES for name in bounds}


class OpportunityListFilterForm(CSRFExemptForm):
    def clean(self):
        """Resolve each date range dropdown into the from/to dates the list filters on."""
        cleaned_data = super().clean()
        for range_name, from_name, to_name in OPPORTUNITY_LIST_DATE_RANGES:
            preset = cleaned_data.get(range_name)
            if preset == DateRanges.CUSTOM:
                if not (cleaned_data.get(from_name) or cleaned_data.get(to_name)):
                    cleaned_data[range_name] = ""
            elif preset:
                cleaned_data[from_name], cleaned_data[to_name] = DateRanges(preset).bounds(now().date())
            else:
                cleaned_data[from_name] = cleaned_data[to_name] = None
        return cleaned_data


class OpportunityListFilterSet(django_filters.FilterSet):
    is_test = YesNoFilter(label="Is Test")
    status = django_filters.MultipleChoiceFilter(
        label="Status",
        choices=OpportunityStatus.choices,
        widget=forms.SelectMultiple(attrs={"data-tomselect": "1"}),
    )
    program = django_filters.MultipleChoiceFilter(
        label="Program", choices=[], widget=forms.SelectMultiple(attrs={"data-tomselect": "1"})
    )
    delivery_type = django_filters.MultipleChoiceFilter(
        label=_("Delivery Type"), choices=[], widget=forms.SelectMultiple(attrs={"data-tomselect": "1"})
    )
    start_date_range = django_filters.ChoiceFilter(
        label="",
        choices=_preset_choices(*_PAST_PRESETS, DateRanges.CUSTOM),
        empty_label=_("Any time"),
        widget=forms.Select(attrs={"x-model": "preset"}),
    )
    start_date_from = django_filters.DateFilter(label="", widget=forms.DateInput(attrs={"type": "date"}))
    start_date_to = django_filters.DateFilter(label="", widget=forms.DateInput(attrs={"type": "date"}))
    end_date_range = django_filters.ChoiceFilter(
        label="",
        choices=_preset_choices(*_PAST_PRESETS, *_FUTURE_PRESETS, DateRanges.CUSTOM),
        empty_label=_("Any time"),
        widget=forms.Select(attrs={"x-model": "preset"}),
    )
    end_date_from = django_filters.DateFilter(label="", widget=forms.DateInput(attrs={"type": "date"}))
    end_date_to = django_filters.DateFilter(label="", widget=forms.DateInput(attrs={"type": "date"}))

    class Meta:
        form = OpportunityListFilterForm

    def __init__(self, *args, **kwargs):
        request = kwargs.pop("request", None)
        super().__init__(*args, **kwargs)

        if request:
            self._set_choices_or_drop("program", [(p.slug, p.name) for p in programs_accessible_to_org(request.org)])
            self._set_choices_or_drop("delivery_type", self._delivery_type_choices(request.org))
        self.form.helper.layout = Layout(
            "status",
            "is_test",
            *[name for name in ("program", "delivery_type") if name in self.filters],
            _date_range_layout(_("Start Date"), "start_date_from", "start_date_to", range_field="start_date_range"),
            _date_range_layout(_("End Date"), "end_date_from", "end_date_to", range_field="end_date_range"),
        )

    def _set_choices_or_drop(self, name, choices):
        if choices:
            self.filters[name].extra["choices"] = choices
        else:
            del self.filters[name]

    @staticmethod
    def _delivery_type_choices(org):
        delivery_types = DeliveryType.objects.filter(
            id__in=opportunities_accessible_to_org(org).filter(archived=False).values("delivery_type_id")
        ).order_by("name")
        return [(str(d.pk), d.name) for d in delivery_types]


def _date_range_layout(label, from_field, to_field, range_field=None):
    """From/To date inputs under a heading; with `range_field`, a preset dropdown (bound to Alpine `preset`) that
    shows them for "Custom range"."""
    heading = HTML(format_html('<p class="block text-gray-700 text-sm font-bold mb-2">{}</p>', label))
    if range_field is None:
        return Div(heading, _from_to_inputs(from_field, to_field), css_class="mb-3")

    # Hidden From/To inputs are disabled too, so a preset doesn't send stale custom dates.
    custom_only = {"x-bind:disabled": f"preset !== '{DateRanges.CUSTOM}'"}
    return Div(
        heading,
        range_field,
        Div(
            _from_to_inputs(Field(from_field, **custom_only), Field(to_field, **custom_only)),
            x_show=f"preset === '{DateRanges.CUSTOM}'",
        ),
        # Seed from the server-rendered selection before x-model takes over the dropdown.
        x_data="{ preset: '' }",
        x_init="preset = $el.querySelector('select').value",
        css_class="mb-3",
    )


def _from_to_inputs(from_field, to_field):
    return Div(
        Div(
            HTML(format_html('<p class="text-gray-600 text-sm mb-1">{}</p>', _("From"))),
            from_field,
            css_class="flex-1",
        ),
        Div(HTML(format_html('<p class="text-gray-600 text-sm mb-1">{}</p>', _("To"))), to_field, css_class="flex-1"),
        css_class="flex gap-2",
    )


TASK_STATUS_CHOICES = [
    (AssignedTaskStatus.ASSIGNED, _("To Do")),
    (AssignedTaskStatus.COMPLETED, _("Completed")),
]


class TasksFilterSet(django_filters.FilterSet):
    worker_name = django_filters.MultipleChoiceFilter(
        label="Worker Name",
        choices=[],
        widget=forms.SelectMultiple(attrs={"data-tomselect": "1"}),
        field_name="user__id",
    )
    task_status = django_filters.MultipleChoiceFilter(
        label=_("Task Status"),
        choices=TASK_STATUS_CHOICES,
        widget=forms.SelectMultiple(attrs={"data-tomselect": "1"}),
        field_name="task_status",
    )
    task_type = django_filters.MultipleChoiceFilter(
        label=_("Task Type"),
        choices=[],
        widget=forms.SelectMultiple(attrs={"data-tomselect": "1"}),
        field_name="task_id",
    )
    date_assigned_from = django_filters.DateFilter(
        label=_("Date Assigned From"),
        widget=forms.DateInput(attrs={"type": "date"}),
        field_name="date_assigned",
        lookup_expr="gte",
    )
    date_assigned_to = django_filters.DateFilter(
        label=_("Date Assigned Before"),
        widget=forms.DateInput(attrs={"type": "date"}),
        field_name="date_assigned",
        lookup_expr="lt",
    )
    due_date_from = django_filters.DateFilter(
        label=_("Due Date From"),
        widget=forms.DateInput(attrs={"type": "date"}),
        field_name="task_due_date",
        lookup_expr="gte",
    )
    due_date_to = django_filters.DateFilter(
        label=_("Due Date Before"),
        widget=forms.DateInput(attrs={"type": "date"}),
        field_name="task_due_date",
        lookup_expr="lt",
    )

    class Meta:
        form = CSRFExemptForm

    def __init__(self, *args, **kwargs):
        self.opportunity = kwargs.pop("opportunity", None)
        super().__init__(*args, **kwargs)
        today = date.today().isoformat()
        self.filters["date_assigned_from"].extra["widget"].attrs["max"] = today
        self.filters["date_assigned_to"].extra["widget"].attrs["max"] = today
        if self.opportunity:
            active_tasks = TaskType.objects.filter(opportunity=self.opportunity, is_active=True)
            self.filters["task_type"].extra["choices"] = [(str(t.pk), t.name) for t in active_tasks]

            worker_queryset = (
                User.objects.filter(
                    opportunityaccess__opportunity=self.opportunity,
                    opportunityaccess__accepted=True,
                )
                .distinct()
                .order_by("name", "username")
            )
            self.filters["worker_name"].extra["choices"] = [
                (str(user.pk), user.display_name_with_username()) for user in worker_queryset
            ]


class UserTasksFilterSet(django_filters.FilterSet):
    task_status = django_filters.MultipleChoiceFilter(
        label=_("Task Status"),
        choices=TASK_STATUS_CHOICES,
        widget=forms.SelectMultiple(attrs={"data-tomselect": "1"}),
        field_name="status",
    )
    task_type = django_filters.MultipleChoiceFilter(
        label=_("Task Type"),
        choices=[],
        widget=forms.SelectMultiple(attrs={"data-tomselect": "1"}),
        field_name="task_type_id",
    )
    date_assigned_from = django_filters.DateFilter(
        label="",
        widget=forms.DateInput(attrs={"type": "date"}),
        field_name="date_created",
        lookup_expr="gte",
    )
    date_assigned_to = django_filters.DateFilter(
        label="",
        widget=forms.DateInput(attrs={"type": "date"}),
        field_name="date_created",
        lookup_expr="lte",
    )
    due_date_from = django_filters.DateFilter(
        label="",
        widget=forms.DateInput(attrs={"type": "date"}),
        field_name="due_date",
        lookup_expr="gte",
    )
    due_date_to = django_filters.DateFilter(
        label="",
        widget=forms.DateInput(attrs={"type": "date"}),
        field_name="due_date",
        lookup_expr="lte",
    )

    class Meta:
        form = CSRFExemptForm

    def __init__(self, *args, **kwargs):
        self.opportunity = kwargs.pop("opportunity", None)
        super().__init__(*args, **kwargs)
        today = date.today().isoformat()
        self.filters["date_assigned_from"].extra["widget"].attrs["max"] = today
        self.filters["date_assigned_to"].extra["widget"].attrs["max"] = today
        if self.opportunity:
            active_tasks = TaskType.objects.filter(opportunity=self.opportunity, is_active=True)
            self.filters["task_type"].extra["choices"] = [(str(t.pk), t.name) for t in active_tasks]
        self.form.helper.layout = Layout(
            "task_status",
            "task_type",
            _date_range_layout(_("Date Assigned"), "date_assigned_from", "date_assigned_to"),
            _date_range_layout(_("Due Date"), "due_date_from", "due_date_to"),
        )


class AssignedTaskFilterSet(django_filters.FilterSet):
    worker_name = django_filters.ChoiceFilter(
        label=_("Worker Name"),
        choices=[],
        field_name="opportunity_access__user__id",
    )
    task_status = django_filters.ChoiceFilter(
        label=_("Task Status"),
        choices=TASK_STATUS_CHOICES,
        field_name="status",
    )
    task_type = django_filters.ChoiceFilter(
        label=_("Task Type"),
        choices=[],
        field_name="task_type__id",
    )
    is_active = django_filters.BooleanFilter(
        label=_("Is Active"),
        field_name="task_type__is_active",
    )
    date_assigned_after = django_filters.DateFilter(
        label=_("Date Assigned From"),
        widget=forms.DateInput(attrs={"type": "date"}),
        field_name="date_created",
        # date_created is a DateTimeField; cast to date before comparing to avoid TZ fragility
        lookup_expr="date__gte",
    )
    date_assigned_before = django_filters.DateFilter(
        label=_("Date Assigned Before"),
        widget=forms.DateInput(attrs={"type": "date"}),
        field_name="date_created",
        # date_created is a DateTimeField; cast to date before comparing to avoid TZ fragility
        lookup_expr="date__lt",
    )
    due_date_after = django_filters.DateFilter(
        label=_("Due Date From"),
        widget=forms.DateInput(attrs={"type": "date"}),
        field_name="due_date",
        lookup_expr="gte",
    )
    due_date_before = django_filters.DateFilter(
        label=_("Due Date Before"),
        widget=forms.DateInput(attrs={"type": "date"}),
        field_name="due_date",
        lookup_expr="lt",
    )

    class Meta:
        form = CSRFExemptForm

    def __init__(self, *args, **kwargs):
        self.opportunity = kwargs.pop("opportunity", None)
        super().__init__(*args, **kwargs)
        today = date.today().isoformat()
        self.filters["date_assigned_after"].extra["widget"].attrs["max"] = today
        self.filters["date_assigned_before"].extra["widget"].attrs["max"] = today
        if self.opportunity:
            self.filters["task_type"].extra["choices"] = self._get_task_type_choices()
            self.filters["worker_name"].extra["choices"] = self._get_worker_name_choices()
        # Layout must be set after dynamic choices are populated above; accessing self.form
        # triggers lazy field creation which caches choices — ChoiceFilter validates strictly.
        self.form.helper.layout = Layout(
            "worker_name",
            "task_status",
            "task_type",
            "is_active",
            Row(Column("date_assigned_after"), Column("date_assigned_before")),
            Row(Column("due_date_after"), Column("due_date_before")),
        )

    def _get_task_type_choices(self):
        """
        Fetch task types with at least one `AssignedTask` for this opportunity
        """
        tasks = TaskType.objects.filter(assignedtask__opportunity_access__opportunity=self.opportunity).distinct()
        return [(str(t.pk), t.name) for t in tasks]

    def _get_worker_name_choices(self):
        """
        Fetch workers with at least one assigned task for this opportunity
        """
        workers = (
            User.objects.filter(
                opportunityaccess__opportunity=self.opportunity,
                opportunityaccess__assignedtask__isnull=False,
            )
            .distinct()
            .order_by("name", "username")
        )
        return [(str(u.pk), u.display_name_with_username()) for u in workers]
