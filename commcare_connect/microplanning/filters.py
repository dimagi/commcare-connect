import django_filters
from django import forms
from django.utils.http import urlencode
from django.utils.translation import gettext_lazy as _

from commcare_connect.microplanning.coverage_progress import CoverageDateFilter
from commcare_connect.microplanning.models import WorkArea, WorkAreaStatus
from commcare_connect.opportunity.filters import CSRFExemptForm
from commcare_connect.opportunity.models import UserVisit
from commcare_connect.users.models import User

INPUT_CSS = (
    "w-full rounded-md border border-gray-300 px-3 py-2 "
    "text-sm shadow-sm focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500"
)


class CoverageFilterForm(CSRFExemptForm):
    """Cross-field validation for the From/To date range: both bounds or neither, and From <= To.

    Without this, a lone or reversed date would be silently ignored (the report falling back to
    Overall) with no feedback; the error is surfaced to the user instead.
    """

    def clean(self):
        cleaned = super().clean()
        if self.errors:  # a field is already invalid (e.g. an unparseable date); don't pile on
            return cleaned
        start, end = cleaned.get("start"), cleaned.get("end")
        if bool(start) != bool(end):
            raise forms.ValidationError(_("Select both a From and a To date to filter by a date range."))
        if start and end and start > end:
            raise forms.ValidationError(_("The From date must be on or before the To date."))
        return cleaned


class CoverageProgressFilterSet(django_filters.FilterSet):
    """Date filter for the Coverage Progress Tracker page.

    Two always-visible date inputs: both empty means Overall (no window), and a complete From/To
    pair applies a custom window. The page's date window is an *aggregation parameter* (it bounds the
    subqueries inside ``CoverageProgressReport``), not a queryset ``.filter()`` — so this FilterSet is
    used only for parsing / validating / rendering the form. ``.qs`` is never evaluated;
    ``to_date_filter()`` maps the cleaned form data onto the existing ``CoverageDateFilter`` modes.
    """

    start = django_filters.DateFilter(
        label=_("From"),
        method="_noop",
        widget=forms.DateInput(attrs={"type": "date", "class": INPUT_CSS}),
    )
    end = django_filters.DateFilter(
        label=_("To"),
        method="_noop",
        widget=forms.DateInput(attrs={"type": "date", "class": INPUT_CSS}),
    )

    class Meta:
        model = WorkArea
        fields = ()
        form = CoverageFilterForm

    def _noop(self, queryset, name, value):
        # The filters never narrow a queryset; the cleaned values are read via to_date_filter().
        return queryset

    def to_date_filter(self) -> CoverageDateFilter:
        """Resolve the submitted form to a CoverageDateFilter. An invalid form (a lone or reversed
        date — see ``CoverageFilterForm``) falls back to overall()."""
        if not self.form.is_valid():
            return CoverageDateFilter.overall()
        cd = self.form.cleaned_data
        if cd.get("start") and cd.get("end"):  # validation guarantees start <= end when both are set
            return CoverageDateFilter(start=cd["start"], end=cd["end"])
        return CoverageDateFilter.overall()

    def active_params(self) -> dict:
        """The applied filter as a plain dict, derived from the resolved date window so download
        links match the on-screen report (an invalid/empty range carries no date params)."""
        date_filter = self.to_date_filter()
        if date_filter.is_overall:
            return {}
        return {"start": date_filter.start.isoformat(), "end": date_filter.end.isoformat()}

    def export_querystring(self, extra=None) -> str:
        """URL-encoded querystring for a download link: the active filter plus the given ``extra``
        export params (the export format/table), so the download matches the on-screen filtered view."""
        return urlencode({**self.active_params(), **(extra or {})})


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
