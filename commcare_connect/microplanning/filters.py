import django_filters
from django import forms
from django.utils.translation import gettext_lazy as _

from commcare_connect.microplanning.models import WorkArea, WorkAreaStatus
from commcare_connect.opportunity.filters import CSRFExemptForm
from commcare_connect.opportunity.models import UserVisit
from commcare_connect.users.models import User

INPUT_CSS = (
    "w-full rounded-md border border-gray-300 px-3 py-2 "
    "text-sm shadow-sm focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500"
)


class WorkAreaMapFilterSet(django_filters.FilterSet):
    status = django_filters.MultipleChoiceFilter(
        label=_("Work Area Status"),
        choices=WorkAreaStatus.choices,
        widget=forms.SelectMultiple(attrs={"data-tomselect": "1", "class": INPUT_CSS}),
    )

    assignee = django_filters.ModelMultipleChoiceFilter(
        label=_("Assignee"),
        field_name="opportunity_access__user",
        queryset=User.objects.none(),
        widget=forms.SelectMultiple(attrs={"data-tomselect": "1", "class": INPUT_CSS}),
    )

    start_date = django_filters.DateFilter(
        label=_("Start Date"),
        field_name="uservisit__visit_date",
        lookup_expr="date__gte",
        widget=forms.DateInput(attrs={"type": "date", "class": INPUT_CSS}),
    )

    end_date = django_filters.DateFilter(
        label=_("End Date"),
        field_name="uservisit__visit_date",
        lookup_expr="date__lte",
        widget=forms.DateInput(attrs={"type": "date", "class": INPUT_CSS}),
    )

    unassigned_only = django_filters.BooleanFilter(
        label=_("Show Only Unassigned"),
        method="filter_unassigned_only",
    )

    class Meta:
        model = WorkArea
        fields = []
        form = CSRFExemptForm

    @property
    def qs(self):
        return super().qs.distinct()

    def __init__(self, *args, opportunity=None, **kwargs):
        super().__init__(*args, **kwargs)
        if opportunity:
            self.filters["assignee"].queryset = (
                User.objects.filter(
                    opportunityaccess__opportunity=opportunity,
                    opportunityaccess__workarea__isnull=False,
                )
                .distinct()
                .order_by("name")
            )

        # Display "name (username)" instead of default __str__
        # which shows email or username (not useful for mobile workers)
        self.filters["assignee"].field.label_from_instance = lambda obj: obj.display_name_with_username()

    def filter_unassigned_only(self, queryset, name, value):
        if value:
            return queryset.filter(opportunity_access__isnull=True)
        return queryset


class UserVisitMapFilterSet(django_filters.FilterSet):
    status = django_filters.MultipleChoiceFilter(
        field_name="work_area__status",
        choices=WorkAreaStatus.choices,
    )

    assignee = django_filters.ModelMultipleChoiceFilter(
        field_name="user",
        queryset=User.objects.none(),
    )

    start_date = django_filters.DateFilter(
        field_name="visit_date",
        lookup_expr="date__gte",
    )

    end_date = django_filters.DateFilter(
        field_name="visit_date",
        lookup_expr="date__lte",
    )

    class Meta:
        model = UserVisit
        fields = []
        form = CSRFExemptForm

    def __init__(self, *args, opportunity=None, **kwargs):
        super().__init__(*args, **kwargs)
        if opportunity:
            self.filters["assignee"].queryset = (
                User.objects.filter(
                    opportunityaccess__opportunity=opportunity,
                    opportunityaccess__workarea__isnull=False,
                )
                .distinct()
                .order_by("name")
            )
