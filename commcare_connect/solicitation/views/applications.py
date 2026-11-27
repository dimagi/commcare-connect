"""The applications an organization's solicitations received."""

from django.utils.decorators import method_decorator
from django_filters.views import FilterView
from django_tables2 import SingleTableMixin
from waffle.mixins import WaffleSwitchMixin

from commcare_connect.flags.switch_names import SOLICITATIONS
from commcare_connect.solicitation.filters import ReceivedApplicationFilterSet
from commcare_connect.solicitation.models import SolicitationApplication
from commcare_connect.solicitation.permissions import solicitation_manager_required
from commcare_connect.solicitation.tables import SolicitationApplicationTable
from commcare_connect.solicitation.views.management import FiltersAppliedMixin
from commcare_connect.utils.tables import get_validated_page_size


@method_decorator(solicitation_manager_required, name="dispatch")
class ReceivedApplicationsView(WaffleSwitchMixin, FiltersAppliedMixin, SingleTableMixin, FilterView):
    """Lists the applications to all of the organization's solicitations, newest first, with their average
    score, recommendations and review progress."""

    waffle_switch = SOLICITATIONS
    table_class = SolicitationApplicationTable
    filterset_class = ReceivedApplicationFilterSet
    template_name = "solicitation/received_applications.html"

    def get_queryset(self):
        return (
            SolicitationApplication.objects.responses()
            .filter(solicitation__organization=self.request.org)
            .with_review_summary()
            .select_related("solicitation", "organization")
            .newest_first()
        )

    def get_paginate_by(self, table_data):
        return get_validated_page_size(self.request)
