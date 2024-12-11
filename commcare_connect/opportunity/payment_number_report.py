import dataclasses
from collections import defaultdict

import django_filters
import django_tables2 as tables
from django.db import transaction
from django.http import HttpResponse
from django.urls import reverse
from django.utils.html import format_html

from commcare_connect.connect_id_client.main import (
    fetch_payment_phone_numbers,
    send_message_bulk,
    update_payment_statuses,
)
from commcare_connect.connect_id_client.models import Message
from commcare_connect.opportunity.models import Opportunity, OpportunityAccess
from commcare_connect.opportunity.views import OrganizationUserMemberRoleMixin
from commcare_connect.organization.models import OrgUserPaymentNumberStatus
from commcare_connect.reports.views import NonModelTableBaseView
from commcare_connect.users.models import User


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
        phone_number = record["phone_number"]

        radio_buttons = [
            f'<input type="radio" name="status_{username}" value="{option}" {"checked" if value == option else ""}> {option.capitalize()}'
            for option in options
        ]
        radio_buttons.append(f'<input type="hidden" name="phone_{username}" value="{phone_number}">')
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
        connecteid_statuses = fetch_payment_phone_numbers(usernames, status)
        # display local status when its overridden
        local_statuses = OrgUserPaymentNumberStatus.objects.filter(
            user__username__in=usernames, organization__name=self.request.org
        )
        local_statuses_by_username = {status.username: status for status in local_statuses}
        for status in connecteid_statuses:
            local_status = local_statuses_by_username.get(status["username"])
            if local_status and local_status.phone_number == status["phone_number"]:
                status["status"] = local_status.status
        return connecteid_statuses

    def post(self, request, *args, **kwargs):
        user_statuses = defaultdict(dict)

        for key, value in request.POST.items():
            if key.startswith("status_"):
                username = key.split("status_")[1]
                user_statuses[username].update({"status": value})
            if key.startswith("phone_"):
                username = key.split("phone_")[1]
                user_statuses[username].update({"phone_number": value})

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
        result = update_payment_number_statuses(updates, opportunity)
        return HttpResponse(
            format_html(
                """<span id="result" class="alert alert-info p-1">{}</span>""".format(
                    f"Approved: {result['approved']}, Rejected: {result['rejected']}, Pending: {result['pending']}"
                )
            )
        )


def update_payment_number_statuses(update_data, opportunity):
    """
    Updates payment number status in bulk
    """

    @dataclasses.dataclass
    class PaymentStatus:
        user: str
        phone_number: str
        current_status: str
        new_status: str
        other_org_statuses: set()

    user_obj_by_username = User.objects.filter(username__in=[u["username"] for u in update_data])
    update_by_username = {
        u["username"]: PaymentStatus(
            user=user_obj_by_username["username"],
            phone_number=u["phone_number"],
            new_status=u["status"],
        )
        for u in update_data
    }

    existing_statuses = OrgUserPaymentNumberStatus.objects.filter(user__username=update_by_username.keys()).all()

    # remove unchanged updates and gather current-status
    #   and status set by other orgs
    for status in existing_statuses:
        update = update_by_username[status.user.username]
        if status.organization == opportunity.organization:
            if update.phone_number == status.phone_number:
                if update.new_status == status.status:
                    # No change in status, so remove it
                    update_by_username.pop(status.user.username)
                else:
                    update.current_status = status.status
            else:
                # the status is for an updated number, so default to PENDING
                update.current_status = OrgUserPaymentNumberStatus.PENDING
        else:
            if update.phone_number == status.phone_number:
                update.other_org_statuses.add(status.status)

    with transaction.atomic():
        objs = [
            OrgUserPaymentNumberStatus(
                organization=opportunity.org,
                user=update_by_username[u["username"]].user,
                phone_number=u.phone_number,
                status=u.new_status,
            )
            for u in update_by_username.values()
        ]
        # Bulk update/create
        objs.bulk_create(objs, update_conflicts=True)

        # Process connect-id updates and push-notifications
        connectid_updates = []
        rejected_usernames = []
        approved_usernames = []
        for update in update_by_username.values():
            if (not update.other_org_statuses) or {update.new_status} == update.other_org_statuses:
                # only send update on connectid if there is no disaggrement bw orgs
                # connectid stores status and triggers relevant notifications
                connectid_updates.append(update)
            else:
                if update.new_status == OrgUserPaymentNumberStatus.REJECTED:
                    rejected_usernames.append("username")
                else:
                    approved_usernames.append("username")

        if connectid_updates:
            response = update_payment_statuses(connectid_updates)
            if response.status not in [200, 201]:
                raise Exception("Error sending payment number status updates to ConnectID")

        rejected_msg = f"{opportunity.name} is unable to send payments to you"
        send_message_bulk(Message(usernames=rejected_usernames, body=rejected_msg))

        approved_msg = f"{opportunity.name} is now able to send payments to you"
        send_message_bulk(Message(usernames=approved_usernames, body=approved_msg))

    return {"approved": len(approved_usernames), "rejected": len(rejected_usernames)}
