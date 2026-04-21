import django_filters
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Column, Layout, Row
from django import forms
from django.utils.translation import gettext_lazy as _

from commcare_connect.opportunity.models import AssignedTaskStatus, OpportunityAccess, TaskType
from commcare_connect.program.models import Program
from commcare_connect.users.models import User


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

    def get_filter_usage_data(self):
        values = self.get_filter_values()
        applied = [k for k, v in values.items() if v not in (None, "", [])]
        if not applied:
            return None

        return {
            "filters": applied,
            "filter_count": len(applied),
            "page_path": self.request.path,
        }

    def filters_applied_count(self):
        return len([v for v in self.get_filter_values().values() if v not in (None, "", [])])

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


class OpportunityListFilterSet(django_filters.FilterSet):
    is_test = YesNoFilter(label="Is Test")
    status = django_filters.MultipleChoiceFilter(
        label="Status",
        choices=[(0, "Active"), (1, "Ended"), (2, "Inactive")],
        widget=forms.SelectMultiple(attrs={"data-tomselect": "1"}),
    )
    program = django_filters.MultipleChoiceFilter(
        label="Program", choices=[], widget=forms.SelectMultiple(attrs={"data-tomselect": "1"})
    )

    class Meta:
        form = CSRFExemptForm

    def __init__(self, *args, **kwargs):
        request = kwargs.pop("request", None)
        super().__init__(*args, **kwargs)

        if request:
            user_programs = Program.objects.filter(organization=request.org)
            if user_programs.exists():
                self.filters["program"].extra["choices"] = [(p.slug, p.name) for p in user_programs]
            else:
                del self.filters["program"]


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
    date_assigned_after = django_filters.DateFilter(
        label=_("Date Assigned From"),
        widget=forms.DateInput(attrs={"type": "date"}),
        field_name="date_assigned",
        lookup_expr="gte",
    )
    date_assigned_before = django_filters.DateFilter(
        label=_("Date Assigned Before"),
        widget=forms.DateInput(attrs={"type": "date"}),
        field_name="date_assigned",
        lookup_expr="lt",
    )
    due_date_after = django_filters.DateFilter(
        label=_("Due Date From"),
        widget=forms.DateInput(attrs={"type": "date"}),
        field_name="task_due_date",
        lookup_expr="gte",
    )
    due_date_before = django_filters.DateFilter(
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
