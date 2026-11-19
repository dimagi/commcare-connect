from functools import reduce
from operator import attrgetter, or_

import django_filters
from django import forms
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from commcare_connect.opportunity.filters import CSRFExemptForm
from commcare_connect.organization.filters import DirectoryFilterSet, ToggleFilter
from commcare_connect.organization.models import Organization
from commcare_connect.solicitation.models import Solicitation


class SolicitationDirectoryFilterSet(DirectoryFilterSet):
    organization = django_filters.ModelMultipleChoiceFilter(
        queryset=Organization.objects.filter(program_manager=True).order_by("name"),
        label=_("Organization"),
        widget=forms.SelectMultiple(attrs={"data-tomselect": "1", "placeholder": _("All Organizations")}),
    )
    type = django_filters.MultipleChoiceFilter(
        choices=Solicitation.Type.choices,
        label=_("Type"),
        widget=forms.SelectMultiple(attrs={"data-tomselect": "1", "placeholder": _("Any Type")}),
    )
    status = django_filters.MultipleChoiceFilter(
        choices=Solicitation.Status.choices,
        method="filter_by_status",
        label=_("Status"),
        widget=forms.SelectMultiple(attrs={"data-tomselect": "1", "placeholder": _("Any Status")}),
    )
    organization_test = ToggleFilter(field_name="organization__is_test", label=_("Test"), off_value=False)

    class Meta:
        form = CSRFExemptForm

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Organization.__str__ is its slug; staff pick organizations by name.
        self.form.fields["organization"].label_from_instance = attrgetter("name")

    def filter_by_search_term(self, queryset, name, value):
        return queryset.filter(
            Q(title__icontains=value) | Q(organization__name__icontains=value) | Q(program__name__icontains=value)
        )

    def filter_by_status(self, queryset, name, value):
        # Matches the status shown in the listing, where an active solicitation past its deadline is closed.
        by_status = {
            Solicitation.Status.DRAFT: queryset.filter(status=Solicitation.Status.DRAFT),
            Solicitation.Status.ACTIVE: queryset.open(),
            Solicitation.Status.CLOSED: queryset.filter(status=Solicitation.Status.CLOSED) | queryset.expired(),
            Solicitation.Status.CANCELLED: queryset.filter(status=Solicitation.Status.CANCELLED),
        }
        return reduce(or_, (by_status[status] for status in value))
