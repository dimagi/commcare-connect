import django_filters
from crispy_forms.helper import FormHelper
from django import forms

from commcare_connect.opportunity.models import (
    OpportunityAccess,
    OpportunityVerificationFlags,
    UserVisit,
    VisitValidationStatus,
)
from commcare_connect.program.models import Program
from commcare_connect.users.models import User
from commcare_connect.utils.flags import FlagLabels, Flags


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
        return len([v for v in self.get_filter_values().values() if v not in (None, "", [])])

    def get_filter_context(self):
        return {
            "filter_form": self.get_filter_form(),
            "filters_applied_count": self.filters_applied_count(),
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


class UserVisitFilterSet(django_filters.FilterSet):
    user = django_filters.ChoiceFilter(
        label="Worker",
        choices=[],
        empty_label="All",
        widget=forms.Select(attrs={"data-tomselect": "1"}),
    )
    visit_date = django_filters.DateFilter(
        label="Visit Date",
        field_name="visit_date",
        lookup_expr="date",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    status = django_filters.MultipleChoiceFilter(
        label="Visit Status",
        choices=[
            (c.value, c.label)
            for c in [VisitValidationStatus.over_limit, VisitValidationStatus.duplicate, VisitValidationStatus.trial]
        ],
        widget=forms.SelectMultiple(attrs={"data-tomselect": "1"}),
    )
    flagged = YesNoFilter(label="Flagged")
    flags = django_filters.MultipleChoiceFilter(
        label="Flags",
        choices=[],
        widget=forms.SelectMultiple(attrs={"data-tomselect": "1"}),
        method="filter_flags",
    )

    class Meta:
        model = UserVisit
        fields = ["user", "visit_date", "status", "flagged", "flags"]
        form = CSRFExemptForm

    def __init__(self, *args, **kwargs):
        opportunity = kwargs.pop("opportunity", None)
        super().__init__(*args, **kwargs)

        if opportunity:
            user_filter = self.filters["user"]
            user_queryset = (
                User.objects.filter(opportunityaccess__opportunity=opportunity).distinct().order_by("name", "username")
            )
            user_choices = [(str(user.id), f"{user.name} ({user.username})") for user in user_queryset]
            user_filter.extra["choices"] = user_choices

            flag_choices = self._get_flag_choices(opportunity)
            if flag_choices:
                self.filters["flags"].extra["choices"] = flag_choices
            else:
                del self.filters["flags"]

    def filter_flags(self, queryset, name, value):
        if not value:
            return queryset
        return queryset.with_any_flags(value)

    def _get_flag_choices(self, opportunity):
        verification_flags = OpportunityVerificationFlags.objects.filter(opportunity=opportunity).first()
        if not verification_flags:
            return []

        enabled_flags = []
        if verification_flags.duplicate:
            enabled_flags.append(Flags.DUPLICATE.value)
        if verification_flags.gps:
            enabled_flags.append(Flags.GPS.value)
        if verification_flags.location and verification_flags.location > 0:
            enabled_flags.append(Flags.LOCATION.value)
        if verification_flags.catchment_areas:
            enabled_flags.append(Flags.CATCHMENT.value)
        if verification_flags.form_submission_start or verification_flags.form_submission_end:
            enabled_flags.append(Flags.FORM_SUBMISSION_PERIOD.value)

        return [(flag, FlagLabels.get_label(flag)) for flag in enabled_flags]
