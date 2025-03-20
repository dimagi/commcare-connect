from datetime import date, datetime, timedelta

import django_filters
import django_tables2 as tables
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Column, Layout, Row
from django import forms
from django.conf import settings
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.db.models import Count, Sum
from django.db.models.functions import TruncDate
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils.functional import cached_property
from django.views.decorators.http import require_GET
from django_filters.views import FilterView

from commcare_connect.opportunity.models import (
    CompletedWork,
    CompletedWorkStatus,
    DeliveryType,
    Opportunity,
    Payment,
    UserVisit,
)
from commcare_connect.organization.models import Organization
from commcare_connect.program.models import Program
from commcare_connect.reports.helpers import get_table_data_for_year_month
from commcare_connect.reports.queries import get_visit_map_queryset

from .tables import AdminReportTable

COUNTRY_CURRENCY_CHOICES = [
    ("ETB", "Ethiopia"),
    ("KES", "Kenya"),
    ("MWK", "Malawi"),
    ("MZN", "Mozambique"),
    ("NGN", "Nigeria"),
]


class DashboardFilters(django_filters.FilterSet):
    program = django_filters.ModelChoiceFilter(
        queryset=DeliveryType.objects.all(),
        field_name="opportunity__delivery_type",
        label="Program",
        empty_label="All Programs",
        required=False,
    )
    organization = django_filters.ModelChoiceFilter(
        queryset=Organization.objects.all(),
        field_name="opportunity__organization",
        label="Organization",
        empty_label="All Organizations",
        required=False,
    )
    from_date = django_filters.DateTimeFilter(
        widget=forms.DateInput(attrs={"type": "date"}),
        field_name="visit_date",
        lookup_expr="gt",
        label="From Date",
        required=False,
    )
    to_date = django_filters.DateTimeFilter(
        widget=forms.DateInput(attrs={"type": "date"}),
        field_name="visit_date__date",
        lookup_expr="lte",
        label="To Date",
        required=False,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.form.helper = FormHelper()
        self.form.helper.form_class = "form-inline"
        self.form.helper.layout = Layout(
            Row(
                Column("program", css_class="col-md-3"),
                Column("organization", css_class="col-md-3"),
                Column("from_date", css_class="col-md-3"),
                Column("to_date", css_class="col-md-3"),
            )
        )

        # Set default values if no data is provided
        if not self.data:
            # Create a mutable copy of the QueryDict
            self.data = self.data.copy() if self.data else {}

            # Set default dates
            today = date.today()
            default_from = today - timedelta(days=30)

            # Set the default values
            self.data["to_date"] = today.strftime("%Y-%m-%d")
            self.data["from_date"] = default_from.strftime("%Y-%m-%d")

            # Force the form to bind with the default data
            self.form.is_bound = True
            self.form.data = self.data

    class Meta:
        model = UserVisit
        fields = ["program", "organization", "from_date", "to_date"]


@login_required
@user_passes_test(lambda u: u.is_superuser)
def program_dashboard_report(request):
    filterset = DashboardFilters(request.GET)
    return render(
        request,
        "reports/dashboard.html",
        context={
            "mapbox_token": settings.MAPBOX_TOKEN,
            "filter": filterset,
        },
    )


@login_required
@user_passes_test(lambda user: user.is_superuser)
@require_GET
def visit_map_data(request):
    filterset = DashboardFilters(request.GET)

    queryset = UserVisit.objects.filter(opportunity__is_test=False)
    if filterset.is_valid():
        queryset = filterset.filter_queryset(queryset)

    queryset = get_visit_map_queryset(queryset)

    # Convert to GeoJSON
    geojson = _results_to_geojson(queryset)

    # Return the GeoJSON as JSON response
    return JsonResponse(geojson, safe=False)


def _results_to_geojson(results):
    geojson = {"type": "FeatureCollection", "features": []}
    status_to_color = {
        "approved": "#4ade80",
        "rejected": "#f87171",
    }
    for i, result in enumerate(results.all()):
        location_str = result.get("location_str")
        # Check if both latitude and longitude are not None and can be converted to float
        if location_str:
            split_location = location_str.split(" ")
            if len(split_location) >= 2:
                try:
                    longitude = float(split_location[1])
                    latitude = float(split_location[0])
                except ValueError:
                    # Skip this result if conversion to float fails
                    continue
            else:
                # Or if the location string is not in the expected format
                continue

            feature = {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [longitude, latitude],
                },
                "properties": {
                    key: value for key, value in result.items() if key not in ["gps_location_lat", "gps_location_long"]
                },
            }
            color = status_to_color.get(result.get("status", ""), "#fbbf24")
            feature["properties"]["color"] = color
            geojson["features"].append(feature)

    return geojson


class SuperUserRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    def test_func(self):
        return self.request.user.is_superuser


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


class DeliveryStatsReportView(tables.SingleTableMixin, SuperUserRequiredMixin, NonModelFilterView):
    table_class = AdminReportTable
    filterset_class = DeliveryReportFilters

    def get_template_names(self):
        if self.request.htmx:
            return ["reports/htmx_table.html"]
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


@login_required
@user_passes_test(lambda u: u.is_superuser)
def dashboard_stats_api(request):
    filterset = DashboardFilters(request.GET)

    # Use the filtered queryset to calculate stats
    visit_queryset = UserVisit.objects.filter(opportunity__is_test=False)
    flw_payment_queryset = Payment.objects.filter(opportunity_access__opportunity__is_test=False)
    org_payment_queryset = Payment.objects.filter(invoice__opportunity__is_test=False)
    completed_work_queryset = CompletedWork.objects.filter(opportunity_access__opportunity__is_test=False)
    if filterset.is_valid():
        visit_queryset = filterset.filter_queryset(visit_queryset)
        raw_filters = filterset.form.cleaned_data
        program = raw_filters.get("program")
        organization = raw_filters.get("organization")
        from_date = raw_filters.get("from_date")
        to_date = raw_filters.get("to_date")

        if program:
            flw_payment_queryset = flw_payment_queryset.filter(opportunity_access__opportunity__delivery_type=program)
            org_payment_queryset = org_payment_queryset.filter(invoice__opportunity__delivery_type=program)
            completed_work_queryset = completed_work_queryset.filter(
                opportunity_access__opportunity__delivery_type=program
            )
        if organization:
            flw_payment_queryset = flw_payment_queryset.filter(
                opportunity_access__opportunity__organization=organization
            )
            org_payment_queryset = org_payment_queryset.filter(invoice__opportunity__organization=organization)
            completed_work_queryset = completed_work_queryset.filter(
                opportunity_access__opportunity__organization=organization
            )
        if from_date:
            flw_payment_queryset = flw_payment_queryset.filter(date_paid__gt=from_date)
            org_payment_queryset = org_payment_queryset.filter(date_paid__gt=from_date)
            # todo: is this the right date to use here?
            completed_work_queryset = completed_work_queryset.filter(status_modified_date__gt=from_date)
        if to_date:
            flw_payment_queryset = flw_payment_queryset.filter(date_paid__date__lte=to_date)
            org_payment_queryset = org_payment_queryset.filter(date_paid__date__lte=to_date)
            completed_work_queryset = completed_work_queryset.filter(status_modified_date__date__lte=to_date)

    # Example stats calculation (adjust based on your needs)
    active_users = visit_queryset.values("opportunity_access__user").distinct().count()
    total_visits = visit_queryset.count()
    verified_visits = visit_queryset.filter(status=CompletedWorkStatus.approved).count()
    percent_verified = round(float(verified_visits / total_visits) * 100, 1) if total_visits > 0 else 0

    total_flw_earnings_usd = (
        completed_work_queryset.aggregate(Sum("saved_payment_accrued_usd"))["saved_payment_accrued_usd__sum"] or 0
    )
    org_earnings_usd = (
        completed_work_queryset.aggregate(Sum("saved_org_payment_accrued_usd"))["saved_org_payment_accrued_usd__sum"]
        or 0
    )
    # org earnings include their share and the money they pass through to FLWs
    total_org_earnings_usd = org_earnings_usd + total_flw_earnings_usd
    total_flw_payments_usd = flw_payment_queryset.aggregate(Sum("amount_usd"))["amount_usd__sum"] or 0
    total_org_payments_usd = org_payment_queryset.aggregate(Sum("amount_usd"))["amount_usd__sum"] or 0

    return JsonResponse(
        {
            "total_visits": f"{total_visits:,}",
            "active_users": f"{active_users:,}",
            "verified_visits": f"{verified_visits:,}",
            "percent_verified": f"{percent_verified:.1f}%",
            "total_flw_earnings_usd": f"${'{:,.0f}'.format(total_flw_earnings_usd)}",
            "total_org_earnings_usd": f"${'{:,.0f}'.format(total_org_earnings_usd)}",
            "total_flw_payments_usd": f"${'{:,.0f}'.format(total_flw_payments_usd)}",
            "total_org_payments_usd": f"${'{:,.0f}'.format(total_org_payments_usd)}",
        }
    )


@login_required
@user_passes_test(lambda u: u.is_superuser)
def dashboard_charts_api(request):
    filterset = DashboardFilters(request.GET)
    queryset = UserVisit.objects.filter(opportunity__is_test=False)
    # Use the filtered queryset if available, else use last 30 days
    if filterset.is_valid():
        queryset = filterset.filter_queryset(queryset)
        from_date = filterset.form.cleaned_data["from_date"]
        to_date = filterset.form.cleaned_data["to_date"]
    else:
        to_date = datetime.now().date()
        from_date = to_date - timedelta(days=30)
        queryset = queryset.filter(visit_date__gte=from_date, visit_date__lte=to_date)

    return JsonResponse(
        {
            "time_series": _get_time_series_data(queryset, from_date, to_date),
            "program_pie": _get_program_pie_data(queryset),
            "status_pie": _get_status_pie_data(queryset),
        }
    )


def _get_time_series_data(queryset, from_date, to_date):
    """Example output:
    {
        "labels": ["Jan 01", "Jan 02", "Jan 03"],
        "datasets": [
            {
                "name": "Program A",
                "data": [5, 3, 7]
            },
            {
                "name": "Program B",
                "data": [2, 4, 1]
            }
        ]
    }
    """
    # Get visits over time by program
    visits_by_program_time = (
        queryset.values(
            "opportunity__delivery_type__name",
            visit_date_date=TruncDate("visit_date"),
        )
        .annotate(count=Count("id"))
        .order_by("visit_date_date", "opportunity__delivery_type__name")
    )

    # Process time series data
    program_data = {}
    for visit in visits_by_program_time:
        program_name = visit["opportunity__delivery_type__name"]
        if program_name not in program_data:
            program_data[program_name] = {}
        program_data[program_name][visit["visit_date_date"]] = visit["count"]
    # Create labels and datasets for time series
    labels = []
    time_datasets = []
    current_date = from_date

    while current_date <= to_date:
        labels.append(current_date.strftime("%b %d"))
        current_date += timedelta(days=1)

    for program_name in program_data.keys():
        data = []
        current_date = from_date
        while current_date <= to_date:
            # Convert current_date to a date object to avoid timezones making comparisons fail
            current_date_date = current_date.date()
            data.append(program_data[program_name].get(current_date_date, 0))
            current_date += timedelta(days=1)

        time_datasets.append({"name": program_name or "Unknown", "data": data})

    return {"labels": labels, "datasets": time_datasets}


def _get_program_pie_data(queryset):
    """Example output:
    {
        "labels": ["Program A", "Program B", "Unknown"],
        "data": [10, 5, 2]
    }
    """
    visits_by_program = (
        queryset.values("opportunity__delivery_type__name").annotate(count=Count("id")).order_by("-count")
    )
    return {
        "labels": [item["opportunity__delivery_type__name"] or "Unknown" for item in visits_by_program],
        "data": [item["count"] for item in visits_by_program],
    }


def _get_status_pie_data(queryset):
    """Example output:
    {
        "labels": ["Approved", "Pending", "Rejected", "Unknown"],
        "data": [15, 8, 4, 1]
    }
    """
    visits_by_status = queryset.values("status").annotate(count=Count("id")).order_by("-count")
    return {
        "labels": [item["status"] or "Unknown" for item in visits_by_status],
        "data": [item["count"] for item in visits_by_status],
    }
