import django_filters
from crispy_forms.layout import Column, Field, Layout, Row
from django.http import JsonResponse
from django.views.generic import TemplateView

from commcare_connect.opportunity.models import Opportunity, UserVisit
from commcare_connect.reports.decorators import KPIReportMixin
from commcare_connect.reports.views import DashboardFilters

from .data import UserVisitData


class UserDashboardFilters(DashboardFilters):
    opportunity = django_filters.ModelChoiceFilter(
        queryset=Opportunity.objects.all(),
        field_name="opportunity",
        label="Opportunity",
        empty_label="All opportunities",
        required=False,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.form.helper.layout = Layout(
            Row(
                Column(Field("program", **{"x-on:change": "fetchData()"}), css_class="col-md-2"),
                Column(Field("organization", **{"x-on:change": "fetchData()"}), css_class="col-md-2"),
                Column(Field("opportunity", **{"x-on:change": "fetchData()"}), css_class="col-md-2"),
                Column(Field("from_date", **{"x-on:change": "fetchData()"}), css_class="col-md-2 ms-auto"),
                Column(Field("to_date", **{"x-on:change": "fetchData()"}), css_class="col-md-2"),
            )
        )

    class Meta:
        model = UserVisit
        fields = ["program", "organization", "opportunity", "from_date", "to_date"]


class UserVisitDashboardView(KPIReportMixin, TemplateView):
    template_name = "reports/uservisit_dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        filterset = UserDashboardFilters(self.request.GET)
        context["filter"] = filterset
        return context

    def get(self, request, *args, **kwargs):
        if request.GET.get("json") == "true":
            return self.get_filtered_data(request)
        return super().get(request, *args, **kwargs)

    def get_filtered_data(self, request):
        filterset = UserDashboardFilters(request.GET)
        data = UserVisitData.get_data(filterset)
        return JsonResponse(data)
