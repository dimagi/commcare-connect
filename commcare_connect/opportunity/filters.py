import django_filters

from commcare_connect.opportunity.models import OpportunityAccess


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

    def _get_filter(self, data=None):
        filter_class = self._get_filter_class()
        if filter_class:
            return filter_class(data, queryset=OpportunityAccess.objects.none())
        return None

    def get_filter_form(self, data=None):
        f = self._get_filter(data)
        if f:
            return f.form
        return None

    def get_filter_values(self, data=None):
        f = self._get_filter(data)
        if f and f.form.is_valid():
            return {name: f.form.cleaned_data.get(name) for name in f.filters.keys()}
        return {}

    def filters_applied_count(self, data=None):
        return len([v for v in self.get_filter_values(data).values() if v not in (None, "")])

    def get_filter_context(self, data):
        return {
            "filter_form": self.get_filter_form(data),
            "filters_applied_count": self.filters_applied_count(data),
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
