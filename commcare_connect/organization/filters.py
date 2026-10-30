import django_filters
from django import forms
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from commcare_connect.opportunity.filters import CSRFExemptForm
from commcare_connect.opportunity.models import Country
from commcare_connect.organization.models import OrganizationStatus, PrimarySector


class ToggleFilter(django_filters.BooleanFilter):
    """A boolean filter shown as a toggle switch, filtering on True when on and on `off_value` when off.

    An `off_value` of None leaves the rows unfiltered while the toggle is off.
    """

    def __init__(self, *args, off_value=None, **kwargs):
        kwargs.setdefault("widget", forms.CheckboxInput(attrs={"class": "simple-toggle"}))
        super().__init__(*args, **kwargs)
        self.off_value = off_value

    def filter(self, qs, value):
        # An unticked checkbox submits False, which stands for "off" rather than a value to filter on.
        return super().filter(qs, True if value else self.off_value)


class AlwaysBoundFilterSet(django_filters.FilterSet):
    """Applies its filters even without data, so each filter's default holds on a plain visit to a listing."""

    def __init__(self, data=None, *args, **kwargs):
        # A filterset with no data skips filtering altogether.
        super().__init__(data if data is not None else {}, *args, **kwargs)


class OrganizationFilterSet(AlwaysBoundFilterSet):
    search = django_filters.CharFilter(
        method="filter_by_search_term",
        label=_("Search"),
        widget=forms.TextInput(attrs={"placeholder": _("Search")}),
    )
    countries = django_filters.ModelMultipleChoiceFilter(
        queryset=Country.objects.order_by("name"),
        label=_("Countries"),
        widget=forms.SelectMultiple(attrs={"data-tomselect": "1", "placeholder": _("All Countries")}),
    )
    primary_sectors = django_filters.ModelMultipleChoiceFilter(
        queryset=PrimarySector.objects.order_by("name"),
        label=_("Sectors"),
        widget=forms.SelectMultiple(attrs={"data-tomselect": "1", "placeholder": _("All Sectors")}),
    )
    status = django_filters.MultipleChoiceFilter(
        choices=OrganizationStatus.choices,
        label=_("Status"),
        widget=forms.SelectMultiple(attrs={"data-tomselect": "1", "placeholder": _("Any Status")}),
    )
    test = ToggleFilter(field_name="is_test", label=_("Test"), off_value=False)

    class Meta:
        form = CSRFExemptForm

    def filter_by_search_term(self, queryset, name, value):
        return queryset.filter(
            Q(name__icontains=value)
            | Q(short_name__icontains=value)
            | Q(countries__name__icontains=value)
            | Q(primary_sectors__name__icontains=value)
        ).distinct()


class ContactFilterSet(AlwaysBoundFilterSet):
    search = django_filters.CharFilter(
        method="filter_by_search_term",
        label=_("Search"),
        widget=forms.TextInput(attrs={"placeholder": _("Search")}),
    )
    countries = django_filters.ModelMultipleChoiceFilter(
        field_name="organization__countries",
        queryset=Country.objects.order_by("name"),
        label=_("Countries"),
        widget=forms.SelectMultiple(attrs={"data-tomselect": "1", "placeholder": _("All Countries")}),
    )
    organization_status = django_filters.MultipleChoiceFilter(
        field_name="organization__status",
        choices=OrganizationStatus.choices,
        label=_("Organization Status"),
        widget=forms.SelectMultiple(attrs={"data-tomselect": "1", "placeholder": _("Any Status")}),
    )
    main_poc = ToggleFilter(field_name="is_main_poc", label=_("Main Point of Contact"))
    archived = ToggleFilter(field_name="is_archived", label=_("Archived"), off_value=False)
    organization_test = ToggleFilter(field_name="organization__is_test", label=_("Test"), off_value=False)

    class Meta:
        form = CSRFExemptForm

    def filter_by_search_term(self, queryset, name, value):
        return queryset.filter(
            Q(name__icontains=value) | Q(organization__name__icontains=value) | Q(email__icontains=value)
        )
