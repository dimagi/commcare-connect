import django_filters
from django import forms
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from commcare_connect.opportunity.filters import CSRFExemptForm
from commcare_connect.opportunity.models import Country
from commcare_connect.organization.models import PrimarySector


class OrganizationFilterSet(django_filters.FilterSet):
    search = django_filters.CharFilter(
        method="filter_by_search_term",
        label=_("Search"),
        widget=forms.TextInput(attrs={"placeholder": _("Search organization, country, sector…")}),
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

    class Meta:
        form = CSRFExemptForm

    def filter_by_search_term(self, queryset, name, value):
        return queryset.filter(
            Q(name__icontains=value)
            | Q(short_name__icontains=value)
            | Q(countries__name__icontains=value)
            | Q(primary_sectors__name__icontains=value)
        ).distinct()
