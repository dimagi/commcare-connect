"""Who takes part in a solicitation, as a PM manages them: invited organizations and the review team."""

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
from commcare_connect.solicitation.forms import (
    SelectedParticipantsForm,
    SolicitationInvitationForm,
    SolicitationReviewerForm,
)
from commcare_connect.solicitation.models import Solicitation
from commcare_connect.solicitation.tables import SolicitationInvitationTable, SolicitationReviewerTable
from commcare_connect.solicitation.tasks import send_reviewer_assignment_emails, send_solicitation_invite_emails
from commcare_connect.solicitation.views.management import SolicitationPageMixin
from commcare_connect.utils.tables import get_validated_page_size


@method_decorator(org_pm_required, name="dispatch")
class SolicitationParticipantsMixin(WaffleSwitchMixin):
    """Who takes part in one of the org's solicitations, as a PM manages them: invited orgs or reviewers."""

    waffle_switch = SOLICITATIONS
    url_name = None  # the page listing the participants; its delete endpoint is named delete_<url_name>
    related_name = None  # the participants' accessor on the solicitation
    solicitations = None  # the solicitations whose participants can be managed

    @cached_property
    def solicitation(self):
        return get_object_or_404(
            self.solicitations, organization=self.request.org, solicitation_id=self.kwargs["solicitation_id"]
        )

    def get_participants(self):
        return getattr(self.solicitation, self.related_name).all()

    def get_success_url(self):
        return self.solicitation_url(self.url_name)

    def solicitation_url(self, url_name):
        return reverse(f"solicitation:{url_name}", args=[self.request.org.slug, self.solicitation.solicitation_id])


class SolicitationParticipantsView(SolicitationParticipantsMixin, SolicitationPageMixin, SingleTableMixin, FormView):
    """Lists a solicitation's participants and adds more through the form in the page's modal.

    The wording a subclass sets is read by the template as `view.<name>`.
    """

    template_name = "solicitation/solicitation_participants.html"
    title = None
    hint = None
    add_title = None
    add_submit = None
    added_message = None
    remove_title = None
    remove_message = None  # __count__ stands for the number of selected participants
    send_emails = None  # called with the new participants' ids once they are committed

    def get_form_kwargs(self):
        return super().get_form_kwargs() | {"solicitation": self.solicitation}

    def get_paginate_by(self, table_data):
        return get_validated_page_size(self.request)

    def get_context_data(self, **kwargs):
        return super().get_context_data(delete_url=self.solicitation_url(f"delete_{self.url_name}"), **kwargs)

    def form_valid(self, form):
        participants = form.new_participants()
        for participant in participants:
            participant.solicitation = self.solicitation
            participant.invited_by = self.request.user
            participant.created_by = participant.modified_by = self.request.user.email
        self.get_participants().bulk_create(participants)
        transaction.on_commit(partial(self.send_emails, [participant.pk for participant in participants]))
        messages.success(self.request, self.added_message % {"count": len(participants)})
        return redirect(self.get_success_url())


class SolicitationParticipantsDeleteView(SolicitationParticipantsMixin, FormView):
    """Removes the participants selected in the table."""

    form_class = SelectedParticipantsForm
    http_method_names = ["post"]
    removed_message = None

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        # Limited to this solicitation's participants, so a posted id from any other solicitation fails validation.
        form.fields["ids"].queryset = self.get_participants()
        return form

    def form_valid(self, form):
        count, _deleted = form.cleaned_data["ids"].delete()
        messages.success(self.request, self.removed_message % {"count": count})
        return redirect(self.get_success_url())

    def form_invalid(self, form):
        return HttpResponseBadRequest()


class InvitationsMixin(SolicitationParticipantsMixin):
    url_name = "invitations"
    related_name = "invitations"
    # Organizations are only invited while a solicitation is open: published and before its deadline.
    solicitations = Solicitation.objects.open()


class SolicitationInvitationsView(InvitationsMixin, SolicitationParticipantsView):
    form_class = SolicitationInvitationForm
    table_class = SolicitationInvitationTable
    title = _("Invited organizations")
    add_title = _("Invite organizations")
    add_submit = _("Invite")
    added_message = _("Invited %(count)d organization(s).")
    remove_title = _("Remove invitations")
    remove_message = _("Remove __count__ invitation(s)? Those organizations will no longer be able to apply.")
    send_emails = staticmethod(send_solicitation_invite_emails)

    @property
    def hint(self):
        if self.solicitation.public:
            return _(
                "This solicitation is public: any organization can apply, and invited organizations' members "
                "get an email asking them to."
            )
        return _(
            "This solicitation is private: only invited organizations can apply. Each invited organization's "
            "members get an email."
        )

    def get_table_data(self):
        return self.get_participants().select_related("organization", "invited_by").order_by("organization__name")


class SolicitationInvitationDeleteView(InvitationsMixin, SolicitationParticipantsDeleteView):
    removed_message = _("Removed %(count)d invitation(s).")


class ReviewersMixin(SolicitationParticipantsMixin):
    url_name = "reviewers"
    related_name = "reviewer_assignments"
    # Reviewers are lined up while drafting and keep scoring after the solicitation closes, so only
    # a cancelled solicitation has no use for them.
    solicitations = Solicitation.objects.exclude(status=Solicitation.Status.CANCELLED)


class SolicitationReviewersView(ReviewersMixin, SolicitationParticipantsView):
    form_class = SolicitationReviewerForm
    table_class = SolicitationReviewerTable
    title = _("Review Team")
    hint = _(
        "Reviewers score this solicitation's applications; observers can only read the scores. "
        "Each person added gets an email."
    )
    add_title = _("Add team members")
    add_submit = _("Add")
    added_message = _("Added %(count)d team member(s).")
    remove_title = _("Remove team members")
    remove_message = _("Remove __count__ team member(s)? They will lose access to this solicitation's applications.")
    send_emails = staticmethod(send_reviewer_assignment_emails)

    def get_table_data(self):
        return self.get_participants().select_related("user", "invited_by").order_by("user__name", "user__email")


class SolicitationReviewerDeleteView(ReviewersMixin, SolicitationParticipantsDeleteView):
    removed_message = _("Removed %(count)d team member(s).")
