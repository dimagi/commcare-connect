from django.contrib.messages.views import SuccessMessageMixin
from django.db.models import Count, Q
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.generic import UpdateView
from django_tables2 import SingleTableView
from waffle.mixins import WaffleSwitchMixin

from commcare_connect.flags.switch_names import SOLICITATIONS
from commcare_connect.organization.decorators import OrgPMRequiredMixin
from commcare_connect.solicitation.forms import SolicitationForm
from commcare_connect.solicitation.models import EDITABLE_STATUSES, Solicitation, SolicitationApplication
from commcare_connect.solicitation.tables import SolicitationTable
from commcare_connect.utils.tables import get_validated_page_size

# Drafts haven't been sent yet and withdrawn applications were taken back, so neither is a response.
NON_RESPONSE_STATUSES = (SolicitationApplication.Status.DRAFT, SolicitationApplication.Status.WITHDRAWN)
STEP_LABELS = {
    "details": _("Details"),
}


class SolicitationListView(WaffleSwitchMixin, OrgPMRequiredMixin, SingleTableView):
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


class SolicitationStepMixin(WaffleSwitchMixin, OrgPMRequiredMixin):
    """A step of creating a solicitation: details, criteria, questions, then review and publish.

    Every step saves to the draft, so a PM can leave and come back. Once active, the criteria and
    questions are locked so applicants answer the same form.
    """

    waffle_switch = SOLICITATIONS
    slug_field = "solicitation_id"
    slug_url_kwarg = "solicitation_id"
    step = None
    next_step = None

    def get_queryset(self):
        return Solicitation.objects.filter(organization=self.request.org, status__in=EDITABLE_STATUSES)

    def get_context_data(self, **kwargs):
        return super().get_context_data(
            step=self.step, step_label=STEP_LABELS[self.step], step_labels=STEP_LABELS, **kwargs
        )

    def get_success_url(self):
        if self.object.structure_locked or self.next_step is None:
            return reverse("solicitation:list", args=[self.request.org.slug])
        return reverse(f"solicitation:{self.next_step}", args=[self.request.org.slug, self.object.solicitation_id])


class SolicitationDetailsView(SuccessMessageMixin, SolicitationStepMixin, UpdateView):
    step = "details"
    success_message = _("Solicitation saved.")
    form_class = SolicitationForm
    template_name = "solicitation/solicitation_step_form.html"

    def get_object(self, queryset=None):
        if self.slug_url_kwarg in self.kwargs:
            return super().get_object(queryset)
        return Solicitation()

    def get_form_kwargs(self):
        return super().get_form_kwargs() | {"organization": self.request.org, "user": self.request.user}
