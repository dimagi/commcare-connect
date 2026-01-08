from datetime import date, timedelta
from urllib.parse import urlencode

import django_filters
import django_tables2 as tables
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Column, Field, Layout, Row
from django import forms
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.db.models import F
from django.http import HttpResponse
from django.urls import reverse
from django.utils.functional import cached_property
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_POST
from django_filters.views import FilterView
from django_tables2.views import SingleTableMixin

from commcare_connect.opportunity.models import CompletedWork, DeliveryType, InvoiceStatus, Opportunity, PaymentInvoice
from commcare_connect.organization.models import Organization
from commcare_connect.program.models import ManagedOpportunity, Program
from commcare_connect.reports.decorators import KPIReportMixin
from commcare_connect.reports.helpers import get_table_data_for_year_month
from commcare_connect.reports.tables import AdminReportTable, InvoiceReportTable
from commcare_connect.reports.tasks import export_invoice_report_task
from commcare_connect.utils.celery import download_export_file, render_export_status
from commcare_connect.utils.permission_const import INVOICE_REPORT_ACCESS
from commcare_connect.utils.tables import DEFAULT_PAGE_SIZE, get_validated_page_size

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


class InvoiceReportFilter(django_filters.FilterSet):
    opportunity_name = django_filters.ModelChoiceFilter(
        field_name="opportunity__name",
        queryset=ManagedOpportunity.objects.only("id", "name"),
        label=_("Opportunity"),
        widget=forms.Select(
            attrs={
                "data-tomselect": "1",
                "placeholder": "Select Opportunity (Name - ID)",
            }
        ),
    )

    status = django_filters.MultipleChoiceFilter(
        choices=InvoiceStatus.choices,
        label=_("Status"),
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
        label=_("From Payment Date"),
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        required=False,
        input_formats=["%Y-%m-%d"],
    )

    to_date = django_filters.DateFilter(
        field_name="date_paid__date",
        lookup_expr="lte",
        label=_("To Payment Date"),
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        required=False,
        input_formats=["%Y-%m-%d"],
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.filters["opportunity_name"].field.label_from_instance = lambda obj: f"{obj.name} - {obj.id}"
        self.form.helper = FormHelper()
        self.form.helper.form_tag = False
        self.form.helper.disable_csrf = True
        self.form.helper.layout = Layout(
            Field("opportunity_name"),
            Field("status"),
            Row(
                Field("from_date"),
                Field("to_date"),
                css_class="grid grid-cols-2 gap-4",
            ),
        )

    class Meta:
        model = PaymentInvoice
        fields = ["opportunity_name", "status", "from_date", "to_date"]


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
        return ["reports/invoice_report.html"]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = "Invoice Report"
        context["task_id"] = self.request.GET.get("task_id")

        if self.filterset:
            filter_fields = self.filterset.form.fields.keys()
            context["filters_applied_count"] = sum(
                1 for key in filter_fields if self.filterset.data.get(key) not in ("", None)
            )
        else:
            context["filters_applied_count"] = 0

        return context

    @classmethod
    def get_invoice_queryset(cls):
        return (
            PaymentInvoice.objects.select_related(
                "opportunity__managedopportunity__program__organization",
                "payment",
            )
            .annotate(
                date_paid=F("payment__date_paid"),
                org_slug=F("opportunity__managedopportunity__program__organization__slug"),
            )
            .order_by("-date")
        )

    def get_queryset(self):
        return self.get_invoice_queryset()


@require_POST
@login_required
@permission_required(INVOICE_REPORT_ACCESS, raise_exception=True)
def export_invoice_report(request):
    filterset = InvoiceReportFilter(request.POST, queryset=PaymentInvoice.objects.none())
    if not filterset.is_valid():
        return HttpResponse("Invalid filters", status=400)

    filters_data = filterset.form.cleaned_data
    if filters_data.get("opportunity_name"):
        filters_data["opportunity_name"] = filters_data["opportunity_name"].id
    task = export_invoice_report_task.delay(filters_data)

    #  Build redirect URL preserving applied filters
    query_params = {k: v for k, v in filters_data.items() if v not in [None, "", []]}
    query_params["task_id"] = task.id
    redirect_url = f"{reverse('reports:invoice_report')}?{urlencode(query_params, doseq=True)}"
    response = HttpResponse(status=204)
    response["HX-Redirect"] = redirect_url
    return response


@require_GET
@login_required
@permission_required(INVOICE_REPORT_ACCESS, raise_exception=True)
def export_status(request, task_id):
    return render_export_status(
        request,
        task_id=task_id,
        download_url=reverse("reports:download_export", args=(task_id,)),
        export_status_url=reverse("reports:export_status", args=(task_id,)),
        ownership_check=None,
    )


@require_GET
@login_required
@permission_required(INVOICE_REPORT_ACCESS, raise_exception=True)
def download_export(request, task_id):
    return download_export_file(task_id=task_id, filename_without_ext=f"invoice_export_{request.user.name}")
