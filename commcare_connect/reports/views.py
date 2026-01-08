from datetime import date, timedelta

import django_filters
import django_tables2 as tables
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Column, Field, Layout, Row
from django import forms
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.db.models import F, OuterRef, Subquery
from django.urls import reverse
from django.utils.functional import cached_property
from django_filters.views import FilterView
from django_tables2.views import SingleTableMixin

from commcare_connect.opportunity.models import (
    CompletedWork,
    DeliveryType,
    InvoiceStatus,
    Opportunity,
    Payment,
    PaymentInvoice,
)
from commcare_connect.organization.models import Organization
from commcare_connect.program.models import Program
from commcare_connect.reports.decorators import KPIReportMixin
from commcare_connect.reports.helpers import get_table_data_for_year_month
from commcare_connect.utils.permission_const import INVOICE_REPORT_ACCESS
from commcare_connect.utils.tables import DEFAULT_PAGE_SIZE, get_validated_page_size

from .tables import AdminReportTable, InvoiceReportTable

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


class NonModelTableBaseView(FilterView):
    # Inherit this for a tabular report
    page_template = "reports/report_table.html"
    htmx_table_template = "reports/htmx_table.html"
    # Override this
    report_title = None

    def get_queryset(self):
        # Doesn't matter which model it is here
        return CompletedWork.objects.none()

    @cached_property
    def filter_values(self):
        if not self.filterset.form.is_valid():
            return None
        else:
            return self.filterset.form.cleaned_data

    def get_template_names(self):
        if self.request.htmx:
            return self.htmx_table_template
        else:
            return self.page_template

    @property
    def object_list(self):
        raise NotImplementedError()

    @property
    def report_url(self):
        raise NotImplementedError()

    def get_context_data(self, *args, **kwargs):
        context = super().get_context_data(**kwargs)
        context["report_url"] = self.report_url
        context["report_title"] = self.report_title
        return context

    def get(self, request, *args, **kwargs):
        filterset_class = self.get_filterset_class()
        self.filterset = self.get_filterset(filterset_class)
        context = self.get_context_data(filter=self.filterset, object_list=self.object_list)
        return self.render_to_response(context)


class DeliveryStatsReportView(tables.SingleTableMixin, KPIReportMixin, NonModelTableBaseView):
    table_class = AdminReportTable
    filterset_class = DeliveryReportFilters
    report_title = "Delivery Stats Report"

    @property
    def report_url(self):
        return reverse("reports:delivery_stats_report")

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


class InvoiceReportFilter(django_filters.FilterSet):
    opportunity_id = django_filters.ModelChoiceFilter(
        field_name="opportunity",
        queryset=Opportunity.objects.all(),
        label="Opportunity",
        widget=forms.Select(
            attrs={
                "data-tomselect": "1",
                "placeholder": "Select Opportunity",
            }
        ),
    )

    status = django_filters.MultipleChoiceFilter(
        choices=InvoiceStatus.choices,
        label="Status",
        widget=forms.SelectMultiple(
            attrs={
                "data-tomselect": "1",
                "placeholder": "Select status",
            }
        ),
    )

    from_date = django_filters.DateFilter(
        field_name="date_paid__date",
        lookup_expr="gte",
        label="From Payment Date",
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        required=False,
        input_formats=["%Y-%m-%d"],
    )

    to_date = django_filters.DateFilter(
        field_name="date_paid__date",
        lookup_expr="lte",
        label="To Payment Date",
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        required=False,
        input_formats=["%Y-%m-%d"],
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.form.helper = FormHelper()
        self.form.helper.form_tag = False
        self.form.helper.disable_csrf = True
        self.form.helper.layout = Layout(
            Field("opportunity_id"),
            Field("status"),
            Row(
                Field("from_date"),
                Field("to_date"),
                css_class="grid grid-cols-2 gap-4",
            ),
        )

    class Meta:
        model = PaymentInvoice
        fields = ["opportunity_id", "status", "from_date", "to_date"]


class InvoiceReportView(
    LoginRequiredMixin,
    PermissionRequiredMixin,
    SingleTableMixin,
    FilterView,
):
    model = PaymentInvoice
    table_class = InvoiceReportTable
    filterset_class = InvoiceReportFilter
    permission_required = INVOICE_REPORT_ACCESS
    paginate_by = DEFAULT_PAGE_SIZE

    def get_paginate_by(self, table):
        return get_validated_page_size(self.request)

    def get_template_names(self):
        if self.request.htmx:
            return ["base_table.html"]
        return ["reports/invoice_report.html"]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = "Invoice Report"

        if self.filterset:
            filter_fields = self.filterset.form.fields.keys()
            context["filters_applied_count"] = sum(
                1 for key in filter_fields if self.filterset.data.get(key) not in ("", None)
            )
        else:
            context["filters_applied_count"] = 0

        return context

    def get_queryset(self):
        payment_date_subquery = Payment.objects.filter(invoice=OuterRef("pk")).values("date_paid")[:1]
        return (
            PaymentInvoice.objects.select_related(
                "opportunity",
                "opportunity__managedopportunity",
                "opportunity__managedopportunity__program",
                "opportunity__managedopportunity__program__organization",
            )
            .annotate(
                date_paid=Subquery(payment_date_subquery),
                org_slug=F("opportunity__managedopportunity__program__organization__slug"),
            )
            .order_by("-date")
        )
