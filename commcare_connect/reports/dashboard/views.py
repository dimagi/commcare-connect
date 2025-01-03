import datetime
import random

from django.http import JsonResponse
from django.shortcuts import render
from django.views.generic import TemplateView

from commcare_connect.opportunity.models import Opportunity

from .data import UserVisitData


def charts_view(request):
    """Main dashboard view"""
    categories = ["sales", "revenue", "users"]
    timeframes = ["monthly", "quarterly", "yearly"]

    context = {
        "categories": categories,
        "timeframes": timeframes,
        "initial_category": "sales",
        "initial_timeframe": "monthly",
    }
    return render(request, "reports/ccc_dashboard.html", context)


def chart_data(request):
    """Dynamic chart data endpoint"""
    category = request.GET.get("category", "sales")
    timeframe = request.GET.get("timeframe", "monthly")

    # Simulated data generation
    data_generators = {
        "sales": {
            "monthly": lambda: [random.randint(50, 100) for _ in range(12)],
            "quarterly": lambda: [random.randint(150, 300) for _ in range(4)],
            "yearly": lambda: [random.randint(1000, 2000)],
        },
        "revenue": {
            "monthly": lambda: [random.randint(5000, 10000) for _ in range(12)],
            "quarterly": lambda: [random.randint(15000, 30000) for _ in range(4)],
            "yearly": lambda: [random.randint(100000, 200000)],
        },
        "users": {
            "monthly": lambda: [random.randint(100, 200) for _ in range(12)],
            "quarterly": lambda: [random.randint(300, 600) for _ in range(4)],
            "yearly": lambda: [random.randint(2000, 4000)],
        },
    }

    # Generate chart data
    chart_data = data_generators[category][timeframe]()

    # Determine x-axis labels
    x_axis_labels = {
        "monthly": ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
        "quarterly": ["Q1", "Q2", "Q3", "Q4"],
        "yearly": ["2024", "2025"],
    }[timeframe]

    return JsonResponse(
        {
            "data": chart_data,
            "x_axis": x_axis_labels,
            "title": f"{category.capitalize()} - {timeframe.capitalize()} View",
        }
    )


class UserVisitDashboardView(TemplateView):
    template_name = "reports/uservisit_dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["opportunities"] = Opportunity.objects.all()
        return context

    def get(self, request, *args, **kwargs):
        if request.GET.get("json") == "true":
            return self.get_filtered_data(request)
        else:
            return super().get(request, *args, **kwargs)

    def get_filtered_data(self, request):
        date_start = request.GET.get("date_start")
        date_end = request.GET.get("date_end")

        # Convert string dates to datetime.date objects if present
        start_date = None
        end_date = None
        if date_start:
            start_date = datetime.datetime.strptime(date_start, "%Y-%m-%d").date()
        if date_end:
            end_date = datetime.datetime.strptime(date_end, "%Y-%m-%d").date()

        opportunities = request.GET.getlist("opportunities")
        # import pdb; pdb.set_trace()
        data = UserVisitData.get_data(
            opportunity_ids=opportunities if opportunities else None, date_gte=start_date, date_lte=end_date
        )

        return JsonResponse(data)
