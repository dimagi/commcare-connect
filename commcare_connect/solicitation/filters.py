import django_filters
from django import forms
from django.utils.translation import gettext_lazy as _

from commcare_connect.opportunity.forms import CHECKBOX_CLASS
from commcare_connect.opportunity.models import Country, DeliveryType
from commcare_connect.solicitation.models import Solicitation
from commcare_connect.utils.datetime import DateRanges
from commcare_connect.utils.filters import CSRFExemptForm, PresetDateRangeFilter


class MarketplaceFilterSet(django_filters.FilterSet):
    type = django_filters.ChoiceFilter(
        label=_("Type"),
        choices=Solicitation.Type.choices,
        empty_label=_("All types"),
        widget=forms.Select(attrs={"class": "base-input"}),
    )
    # Matching any of several countries or delivery types filters on a forward foreign key, which can't repeat a
    # row, so the DISTINCT django-filter adds by default isn't needed.
    country = django_filters.ModelMultipleChoiceFilter(
        label=_("Country"),
        queryset=Country.objects.none(),
        distinct=False,
        widget=forms.SelectMultiple(attrs={"data-tomselect": "1", "placeholder": _("All countries")}),
    )
    delivery_type = django_filters.ModelMultipleChoiceFilter(
        label=_("Delivery type"),
        queryset=DeliveryType.objects.none(),
        distinct=False,
        widget=forms.SelectMultiple(attrs={"data-tomselect": "1", "placeholder": _("All delivery types")}),
    )
    # The deadline's date in UTC, so a range ending on a day takes in deadlines at any time that day.
    deadline = PresetDateRangeFilter(
        label=_("Deadline"),
        field_name="application_deadline__date",
        presets=[DateRanges.NEXT_30_DAYS, DateRanges.NEXT_3_MONTHS, DateRanges.NEXT_6_MONTHS],
    )

    class Meta:
        form = CSRFExemptForm

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Only values some listed solicitation uses: any other option would always come back empty, and one taken
        # from a private or closed solicitation would give away that it exists.
        listed = Solicitation.objects.public()
        self._set_queryset_or_drop("country", Country.objects.filter(code__in=listed.values("country")))
        self._set_queryset_or_drop("delivery_type", DeliveryType.objects.filter(pk__in=listed.values("delivery_type")))

    def _set_queryset_or_drop(self, name, queryset):
        """An empty select offers nothing to pick, so the filter is left out instead, unless a value was submitted
        for it (a saved link, say): that value is now no option, so the filter stays to refuse it."""
        queryset = queryset.order_by("name")
        if queryset.exists() or self._submitted(name):
            self.filters[name].queryset = queryset
        else:
            del self.filters[name]

    def _submitted(self, name):
        """Whether the request picked any value for the multi-select ``name``."""
        return any(forms.SelectMultiple().value_from_datadict(self.data, {}, name) or [])


class TableFilterSet(django_filters.FilterSet):
    """Filters shown in a row above a solicitation table, styled like the app's other inputs."""

    class Meta:
        form = CSRFExemptForm

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.form.fields.values():
            field.widget.attrs.setdefault("class", "base-input base-input-sm")


def shortlisted_only_filter():
    return django_filters.BooleanFilter(
        label=_("Shortlisted only"),
        widget=forms.CheckboxInput(attrs={"class": CHECKBOX_CLASS}),
        method=lambda queryset, name, value: queryset.filter(shortlisted=True) if value else queryset,
    )


class ReceivedApplicationFilterSet(TableFilterSet):
    solicitation = django_filters.ModelChoiceFilter(
        label=_("Solicitation"),
        queryset=lambda request: Solicitation.objects.filter(organization=request.org).order_by("title"),
        to_field_name="solicitation_id",
        empty_label=_("All solicitations"),
    )
    shortlisted = shortlisted_only_filter()
