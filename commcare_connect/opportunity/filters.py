import django_filters
from django import forms

from commcare_connect.opportunity.models import OpportunityAccess
from commcare_connect.program.models import Program


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
        filter_class = self._get_filter_class()
        if filter_class:
            return filter_class(self.request.GET, **self.get_filter_kwargs())
        return None

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

    def filters_applied_count(self):
        return len([v for v in self.get_filter_values().values() if v not in (None, "")])

    def get_filter_context(self):
        return {
            "filter_form": self.get_filter_form(),
            "filters_applied_count": self.filters_applied_count(),
        }


YES_OR_NO_CHOICES = (
    (True, "Yes"),
    (False, "No"),
)


class YesNoFilter(django_filters.ChoiceFilter):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("choices", YES_OR_NO_CHOICES)
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

    def __init__(self, *args, **kwargs):
        request = kwargs.pop("request", None)
        super().__init__(*args, **kwargs)

        if request:
            user_programs = Program.objects.filter(organization=request.org)
            self.filters["program"].extra["choices"] = [(p.slug, p.name) for p in user_programs]
