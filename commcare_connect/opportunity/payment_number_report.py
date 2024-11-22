from collections import defaultdict

import django_filters
import django_tables2 as tables
from django.http import HttpResponse
from django.urls import reverse
from django.utils.html import format_html

from commcare_connect.connect_id_client.main import fetch_payment_phone_numbers, validate_payment_profiles
from commcare_connect.opportunity.models import Opportunity, OpportunityAccess
from commcare_connect.opportunity.views import OrganizationUserMemberRoleMixin
from commcare_connect.reports.views import NonModelTableBaseView


class PaymentNumberReportTable(tables.Table):
    username = tables.Column(verbose_name="Username")
    phone_number = tables.Column(verbose_name="Payment Phone Number")
    status = tables.Column(verbose_name="Status")

    class Meta:
        template_name = "opportunity/payment_numbers_table.html"
        attrs = {"class": "table table-striped table-hover"}
        empty_text = "No data available."
        orderable = False

    def render_status(self, value, record):
        options = ["pending", "approved", "rejected"]
        username = record["username"]

        radio_buttons = [
            f'<input type="radio" name="status_{username}" value="{option}" {"checked" if value == option else ""}> {option.capitalize()}'
            for option in options
        ]
        return format_html("<br>".join(radio_buttons))


class PaymentFilters(django_filters.FilterSet):
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("approved", "Approved"),
        ("rejected", "Rejected"),
    ]

    opportunity = django_filters.ChoiceFilter(method="filter_by_ignore")
    status = django_filters.ChoiceFilter(choices=STATUS_CHOICES, label="Payment Status")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        opportunities = Opportunity.objects.filter(
            organization=self.request.org, payment_info_required=True
        ).values_list("id", "name")
        self.filters["opportunity"] = django_filters.ChoiceFilter(
            choices=opportunities, label="Opportunity", empty_label=None
        )

    class Meta:
        model = None
        fields = ["status"]

    def filter_by_ignore(self, queryset, name, value):
        return queryset


class PaymentNumberReport(tables.SingleTableMixin, OrganizationUserMemberRoleMixin, NonModelTableBaseView):
    table_class = PaymentNumberReportTable
    filterset_class = PaymentFilters
    htmx_table_template = "opportunity/payment_numbers_table.html"
    report_title = "Verify Payment Phone Numbers"

    @property
    def report_url(self):
        return reverse("opportunity:payment_number_report", args=(self.request.org.slug,))

    @property
    def object_list(self):
        if not self.filter_values:
            return []

        status = self.filter_values["status"]
        opportunity = self.filter_values["opportunity"]
        if not opportunity:
            return []
        usernames = OpportunityAccess.objects.filter(opportunity_id=opportunity).values_list(
            "user__username", flat=True
        )
        return fetch_payment_phone_numbers(usernames, status)

    def post(self, request, *args, **kwargs):
        user_statuses = defaultdict(dict)
        for key, value in request.POST.items():
            if key.startswith("status_"):
                username = key.split("status_")[1]
                user_statuses[username].update({"status": value})

        # validate that usernames do belong to this opportunity
        opportunity_id = request.GET.get("opportunity")
        if not opportunity_id:
            return HttpResponse("Opportunity must be specified", status=400)
        opportunity = Opportunity.objects.get(pk=opportunity_id)
        if opportunity.organization != request.org:
            return HttpResponse("You can't specify this opportunity", status=400)
        is_valid = OpportunityAccess.objects.filter(
            opportunity_id=opportunity_id, user__username__in=user_statuses.keys()
        ).count() == len(user_statuses)
        if not is_valid:
            return HttpResponse("Unknown usernames", status=400)

        updates = [{"username": username, **values} for username, values in user_statuses.items()]
        result = validate_payment_profiles(updates)
        return HttpResponse(
            format_html(
                """<span id="result" class="alert alert-info p-1">{}</span>""".format(
                    f"Approved: {result['approved']}, Rejected: {result['rejected']}, Pending: {result['pending']}"
                )
            )
        )
