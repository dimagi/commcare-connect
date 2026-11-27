"""The organizations invited to a solicitation."""

from functools import cached_property, partial

from django.contrib import messages
from django.db import transaction
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.views.generic import FormView
from django_tables2 import SingleTableMixin
from waffle.mixins import WaffleSwitchMixin

from commcare_connect.flags.switch_names import SOLICITATIONS
from commcare_connect.organization.decorators import org_pm_required
from commcare_connect.solicitation.forms import SolicitationInvitationDeleteForm, SolicitationInvitationForm
from commcare_connect.solicitation.models import Solicitation, SolicitationInvitation
from commcare_connect.solicitation.tables import SolicitationInvitationTable
from commcare_connect.solicitation.tasks import send_solicitation_invite_emails
from commcare_connect.solicitation.views.management import SolicitationPageMixin
from commcare_connect.utils.tables import get_validated_page_size


@method_decorator(org_pm_required, name="dispatch")
class InvitationsMixin(WaffleSwitchMixin):
    waffle_switch = SOLICITATIONS

    @cached_property
    def solicitation(self):
        # Organizations are only invited while a solicitation is open: published and before its deadline.
        return get_object_or_404(
            Solicitation.objects.open(),
            organization=self.request.org,
            solicitation_id=self.kwargs["solicitation_id"],
        )

    def get_form_kwargs(self):
        return super().get_form_kwargs() | {"solicitation": self.solicitation}

    def get_success_url(self):
        return reverse("solicitation:invitations", args=[self.request.org.slug, self.solicitation.solicitation_id])


class SolicitationInvitationsView(InvitationsMixin, SolicitationPageMixin, SingleTableMixin, FormView):
    """Lists the organizations invited to a solicitation and invites more."""

    title = _("Invited organizations")
    form_class = SolicitationInvitationForm
    template_name = "solicitation/solicitation_invitations.html"
    table_class = SolicitationInvitationTable

    def get_table_data(self):
        return self.solicitation.invitations.select_related("organization", "invited_by").order_by(
            "organization__name"
        )

    def get_paginate_by(self, table_data):
        return get_validated_page_size(self.request)

    def form_valid(self, form):
        email = self.request.user.email
        invitations = SolicitationInvitation.objects.bulk_create(
            SolicitationInvitation(
                solicitation=self.solicitation,
                organization=organization,
                invited_by=self.request.user,
                created_by=email,
                modified_by=email,
            )
            for organization in form.cleaned_data["organizations"]
        )
        transaction.on_commit(partial(send_solicitation_invite_emails, [invitation.pk for invitation in invitations]))
        messages.success(self.request, _("Invited %(count)d organization(s).") % {"count": len(invitations)})
        return redirect(self.get_success_url())


class SolicitationInvitationDeleteView(InvitationsMixin, FormView):
    """Removes the invitations selected in the table."""

    form_class = SolicitationInvitationDeleteForm
    http_method_names = ["post"]

    def form_valid(self, form):
        count, _deleted = form.cleaned_data["invitation_ids"].delete()
        messages.success(self.request, _("Removed %(count)d invitation(s).") % {"count": count})
        return redirect(self.get_success_url())

    def form_invalid(self, form):
        return HttpResponseBadRequest()
