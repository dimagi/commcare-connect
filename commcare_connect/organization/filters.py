import django_filters
from django import forms
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from commcare_connect.opportunity.filters import CSRFExemptForm
from commcare_connect.opportunity.models import Country
from commcare_connect.organization.models import OrganizationStatus, PrimarySector


class OptInFilterSet(django_filters.FilterSet):
    """Leaves out the rows behind each boolean filter in `opt_in_filters` until that filter is set."""

    opt_in_filters = ()

    def __init__(self, data=None, *args, **kwargs):
        # A filterset with no data skips filtering altogether, which would list the opted-out rows
        # on a plain visit to the listing, so it is always bound.
        super().__init__(data if data is not None else {}, *args, **kwargs)

    def filter_queryset(self, queryset):
        for name in self.opt_in_filters:
            if not self.form.cleaned_data.get(name):
                queryset = queryset.filter(**{self.filters[name].field_name: False})
        return super().filter_queryset(queryset)


class OrganizationFilterSet(django_filters.FilterSet):
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

    class Meta:
        form = CSRFExemptForm

    def filter_by_search_term(self, queryset, name, value):
        return queryset.filter(
            Q(name__icontains=value)
            | Q(short_name__icontains=value)
            | Q(countries__name__icontains=value)
            | Q(primary_sectors__name__icontains=value)
        ).distinct()


class ContactFilterSet(OptInFilterSet):
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
    main_poc = django_filters.BooleanFilter(
        field_name="is_main_poc",
        label=_("Contacts"),
        widget=forms.Select(
            choices=[("", _("Any Contacts")), ("true", _("Main POC Only"))],
            attrs={"data-tomselect": "1", "data-tomselect:no-remove-button": "1"},
        ),
    )
    archived = django_filters.BooleanFilter(
        field_name="is_archived",
        label=_("Show"),
        widget=forms.Select(
            choices=[("", _("Active")), ("true", _("Archived"))],
            attrs={"data-tomselect": "1", "data-tomselect:no-remove-button": "1"},
        ),
    )

    opt_in_filters = ("archived",)

    class Meta:
        form = CSRFExemptForm

    def filter_by_search_term(self, queryset, name, value):
        return queryset.filter(
            Q(name__icontains=value) | Q(organization__name__icontains=value) | Q(email__icontains=value)
        )
