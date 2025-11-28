from datetime import date, timedelta

import django_filters
import django_tables2 as tables
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Column, Layout, Row
from django.urls import reverse
from django.utils.functional import cached_property
from django_filters.views import FilterView

from commcare_connect.opportunity.models import CompletedWork, DeliveryType, Opportunity
from commcare_connect.organization.models import Organization
from commcare_connect.program.models import Program
from commcare_connect.reports.decorators import KPIReportMixin
from commcare_connect.reports.helpers import get_table_data_for_year_month

from .tables import AdminReportTable

COUNTRY_CURRENCY_CHOICES = [
    ("ETB", "Ethiopia"),
    ("KES", "Kenya"),
    ("MWK", "Malawi"),
    ("MZN", "Mozambique"),
    ("NGN", "Nigeria"),
]


class DeliveryReportFilters(django_filters.FilterSet):
    delivery_type = django_filters.ChoiceFilter(
        choices=DeliveryType.objects.values_list("slug", "name"),
        label="Delivery Type",
    )
    program = django_filters.ModelChoiceFilter(
        queryset=Program.objects.all(),
        label="Program",
    )
    network_manager = django_filters.ModelChoiceFilter(
        queryset=Organization.objects.filter(program_manager=False),
        label="Network Manager",
    )
    opportunity = django_filters.ModelChoiceFilter(
        queryset=Opportunity.objects.filter(is_test=False),
        label="Opportunity",
    )
    country_currency = django_filters.ChoiceFilter(choices=COUNTRY_CURRENCY_CHOICES, label="Country")
    from_date = django_filters.DateFilter(
        label="From Date",
        required=False,
        input_formats=["%Y-%m"],
    )
    to_date = django_filters.DateFilter(
        label="To Date",
        required=False,
        input_formats=["%Y-%m"],
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.form.helper = FormHelper()
        self.form.helper.form_class = "form-inline"
        self.form.helper.layout = Layout(
            Row(
                Column("program", css_class="col-md-3"),
                Column("network_manager", css_class="col-md-3"),
                Column("opportunity", css_class="col-md-3"),
                Column("country_currency", css_class="col-md-3"),
            ),
            Row(
                Column("delivery_type", css_class="col-md-4"),
                Column("from_date", css_class="col-md-4"),
                Column("to_date", css_class="col-md-4"),
            ),
        )

        if not self.data:
            self.data = self.data.copy() if self.data else {}
            today = date.today()
            default_from = today - timedelta(days=30)
            self.data["to_date"] = today.strftime("%Y-%m")
            self.data["from_date"] = default_from.strftime("%Y-%m")
            self.form.is_bound = True
            self.form.data = self.data

    class Meta:
        model = None
        fields = [
            "delivery_type",
            "from_date",
            "to_date",
            "program",
            "network_manager",
            "opportunity",
        ]
        unknown_field_behavior = django_filters.UnknownFieldBehavior.IGNORE


class NonModelFilterView(FilterView):
    def get_queryset(self):
        # Doesn't matter which model it is here
        return CompletedWork.objects.none()

    @property
    def object_list(self):
        # Override this
        return []

    def get(self, request, *args, **kwargs):
        filterset_class = self.get_filterset_class()
        self.filterset = self.get_filterset(filterset_class)
        context = self.get_context_data(filter=self.filterset, object_list=self.object_list)
        return self.render_to_response(context)


class DeliveryStatsReportView(tables.SingleTableMixin, KPIReportMixin, NonModelFilterView):
    table_class = AdminReportTable
    filterset_class = DeliveryReportFilters

    def get_template_names(self):
        if self.request.htmx:
            return ["base_table.html"]
        return ["reports/report_table.html"]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["report_url"] = reverse("reports:delivery_stats_report")
        return context

    @cached_property
    def filter_values(self):
        filters = {}
        if self.filterset.form.is_valid():
            filters.update(self.filterset.form.cleaned_data)
        return filters

    @property
    def object_list(self):
        return get_table_data_for_year_month(**self.filter_values)
