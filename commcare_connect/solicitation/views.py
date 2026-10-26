from django.db.models import Count, Q
from django.utils.decorators import method_decorator
from django_tables2 import SingleTableView
from waffle.mixins import WaffleSwitchMixin

from commcare_connect.flags.switch_names import SOLICITATIONS
from commcare_connect.organization.decorators import org_pm_required
from commcare_connect.solicitation.models import Solicitation, SolicitationApplication
from commcare_connect.solicitation.tables import SolicitationTable
from commcare_connect.utils.tables import get_validated_page_size

# Drafts haven't been sent yet and withdrawn applications were taken back, so neither is a response.
NON_RESPONSE_STATUSES = (SolicitationApplication.Status.DRAFT, SolicitationApplication.Status.WITHDRAWN)


@method_decorator(org_pm_required, name="dispatch")
class SolicitationListView(WaffleSwitchMixin, SingleTableView):
    waffle_switch = SOLICITATIONS
    table_class = SolicitationTable
    template_name = "solicitation/solicitation_list.html"

    def get_paginate_by(self, table_data):
        return get_validated_page_size(self.request)

    def get_queryset(self):
        return org_solicitations(self.request.org)


def org_solicitations(organization):
    return (
        Solicitation.objects.filter(organization=organization)
        .annotate(response_count=Count("applications", filter=~Q(applications__status__in=NON_RESPONSE_STATUSES)))
        .order_by("-date_created")
    )
