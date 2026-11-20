"""The PM's view of the applications their organization's solicitations received."""

from functools import cached_property

from django.shortcuts import get_object_or_404
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django_filters.views import FilterView
from django_tables2 import SingleTableMixin
from waffle.mixins import WaffleSwitchMixin

from commcare_connect.flags.switch_names import SOLICITATIONS
from commcare_connect.organization.decorators import org_pm_required
from commcare_connect.solicitation.filters import ReceivedApplicationFilterSet, SolicitationApplicationFilterSet
from commcare_connect.solicitation.models import Solicitation, SolicitationApplication, SolicitationReviewer
from commcare_connect.solicitation.tables import ReceivedApplicationTable, SolicitationApplicationTable
from commcare_connect.solicitation.views.management import SolicitationPageMixin
from commcare_connect.utils.tables import get_validated_page_size


@method_decorator(org_pm_required, name="dispatch")
class SolicitationApplicationsView(WaffleSwitchMixin, SolicitationPageMixin, SingleTableMixin, FilterView):
    """Lists a solicitation's applications with their reviewer-averaged score, recommendations and review progress."""

    waffle_switch = SOLICITATIONS
    table_class = SolicitationApplicationTable
    filterset_class = SolicitationApplicationFilterSet
    template_name = "solicitation/solicitation_applications.html"
    title = _("Applications")

    @cached_property
    def solicitation(self):
        return get_object_or_404(
            Solicitation, organization=self.request.org, solicitation_id=self.kwargs["solicitation_id"]
        )

    @cached_property
    def reviewer_ids(self):
        # Observers don't score, so only reviewers count towards review progress.
        return list(
            self.solicitation.reviewer_assignments.filter(role=SolicitationReviewer.Role.REVIEWER).values_list(
                "user_id", flat=True
            )
        )

    def get_queryset(self):
        return (
            self.solicitation.applications.responses()
            .with_review_summary(reviewer_ids=self.reviewer_ids)
            .select_related("organization")
            .newest_first()
        )

    def get_paginate_by(self, table_data):
        return get_validated_page_size(self.request)

    def get_table_kwargs(self):
        return {"reviewer_count": len(self.reviewer_ids)}


@method_decorator(org_pm_required, name="dispatch")
class ReceivedApplicationsView(WaffleSwitchMixin, SingleTableMixin, FilterView):
    """Lists the applications to all of the organization's solicitations, newest first."""

    waffle_switch = SOLICITATIONS
    table_class = ReceivedApplicationTable
    filterset_class = ReceivedApplicationFilterSet
    template_name = "solicitation/received_applications.html"

    def get_queryset(self):
        return (
            SolicitationApplication.objects.responses()
            .filter(solicitation__organization=self.request.org)
            .select_related("solicitation", "organization")
            .newest_first()
        )

    def get_paginate_by(self, table_data):
        return get_validated_page_size(self.request)
