from functools import cached_property, partial

from django.contrib import messages
from django.contrib.messages.views import SuccessMessageMixin
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count, Prefetch, Q
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.views.generic import FormView, UpdateView
from django_tables2 import SingleTableMixin, SingleTableView
from waffle.mixins import WaffleSwitchMixin

from commcare_connect.flags.switch_names import SOLICITATIONS
from commcare_connect.organization.decorators import org_pm_required
from commcare_connect.solicitation.forms import (
    CriterionInlineFormSet,
    QuestionInlineFormSet,
    SelectedParticipantsForm,
    SolicitationClosureForm,
    SolicitationForm,
    SolicitationInvitationForm,
    SolicitationPublishForm,
    SolicitationReviewerForm,
    publish_errors,
)
from commcare_connect.solicitation.models import (
    Solicitation,
    SolicitationApplication,
    SolicitationEvaluationCriterion,
    SolicitationQuestion,
)
from commcare_connect.solicitation.tables import (
    SolicitationInvitationTable,
    SolicitationReviewerTable,
    SolicitationTable,
)
from commcare_connect.solicitation.tasks import send_reviewer_assignment_emails, send_solicitation_invite_emails
from commcare_connect.utils.tables import get_validated_page_size
from commcare_connect.utils.views import HtmxModalFormMixin

# Drafts haven't been sent yet and withdrawn applications were taken back, so neither is a response.
NON_RESPONSE_STATUSES = (SolicitationApplication.Status.DRAFT, SolicitationApplication.Status.WITHDRAWN)
STEP_LABELS = {
    "details": _("Details"),
    "criteria": _("Evaluation criteria"),
    "questions": _("Questions"),
    "publish": _("Review & publish"),
}


@method_decorator(org_pm_required, name="dispatch")
class SolicitationListView(WaffleSwitchMixin, SingleTableView):
    waffle_switch = SOLICITATIONS
    table_class = SolicitationTable
    template_name = "solicitation/solicitation_list.html"

    def get_paginate_by(self, table_data):
        return get_validated_page_size(self.request)

    def get_queryset(self):
        return (
            Solicitation.objects.filter(organization=self.request.org)
            # distinct, since counting several relations joins them all and would multiply the counts together.
            .annotate(
                response_count=Count(
                    "applications", filter=~Q(applications__status__in=NON_RESPONSE_STATUSES), distinct=True
                ),
                invited_count=Count("invitations", distinct=True),
                reviewer_count=Count("reviewer_assignments", distinct=True),
            )
            .order_by("-date_created")
        )


@method_decorator(org_pm_required, name="dispatch")
class EditableSolicitationMixin(WaffleSwitchMixin):
    """Looks up one of the org's solicitations that can still change; closed and cancelled ones are not found."""

    waffle_switch = SOLICITATIONS
    slug_field = "solicitation_id"
    slug_url_kwarg = "solicitation_id"

    def get_queryset(self):
        return Solicitation.objects.editable().filter(organization=self.request.org)


class SolicitationStatusChangeMixin:
    """Moves the solicitation to `status` when the form is saved, recording who did it."""

    status = None

    def form_valid(self, form):
        form.instance.status = self.status
        form.instance.modified_by = self.request.user.email
        return super().form_valid(form)


class SolicitationStepMixin(EditableSolicitationMixin):
    """A step of creating a solicitation: details, criteria, questions, then review and publish.

    Every step saves to the draft, so a PM can leave and come back. Once active, the criteria and
    questions are locked so applicants answer the same form.
    """

    step = None
    previous_step = None
    next_step = None

    def get_context_data(self, **kwargs):
        kwargs["path"] = [
            {"title": _("Solicitations"), "url": reverse("solicitation:list", args=[self.request.org.slug])},
            {"title": self.object.title if self.object.pk else _("New solicitation")},
        ]
        if self.previous_step:
            kwargs["previous_step_url"] = reverse(
                f"solicitation:{self.previous_step}", args=[self.request.org.slug, self.object.solicitation_id]
            )
        return super().get_context_data(
            step=self.step, step_label=STEP_LABELS[self.step], step_labels=STEP_LABELS, **kwargs
        )

    def get_success_url(self):
        if self.object.structure_locked:
            return reverse("solicitation:list", args=[self.request.org.slug])
        return reverse(f"solicitation:{self.next_step}", args=[self.request.org.slug, self.object.solicitation_id])


class SolicitationDetailsView(SuccessMessageMixin, SolicitationStepMixin, UpdateView):
    step = "details"
    next_step = "criteria"
    success_message = _("Solicitation saved.")
    form_class = SolicitationForm
    template_name = "solicitation/solicitation_step_form.html"

    def get_object(self, queryset=None):
        if self.slug_url_kwarg in self.kwargs:
            return super().get_object(queryset)
        return Solicitation()

    def get_form_kwargs(self):
        return super().get_form_kwargs() | {"organization": self.request.org, "user": self.request.user}


class SolicitationFormSetStepView(SolicitationStepMixin, UpdateView):
    """Edits the solicitation's criteria or questions as a formset; read-only once locked."""

    template_name = "solicitation/solicitation_step_form.html"

    def get_form(self, form_class=None):
        formset = super().get_form(form_class)
        if self.object.structure_locked:
            for form in formset:
                for field in form.fields.values():
                    field.disabled = True
        return formset

    def form_valid(self, formset):
        if self.object.structure_locked:
            raise PermissionDenied
        # Not super(): ModelFormMixin would replace self.object with the list formset.save() returns.
        for form in formset:
            form.instance.created_by = form.instance.created_by or self.request.user.email
            form.instance.modified_by = self.request.user.email
        formset.save()
        messages.success(self.request, _("Solicitation saved."))
        return redirect(self.get_success_url())


class SolicitationCriteriaView(SolicitationFormSetStepView):
    step = "criteria"
    previous_step = "details"
    next_step = "questions"
    form_class = CriterionInlineFormSet


class SolicitationQuestionsView(SolicitationFormSetStepView):
    step = "questions"
    previous_step = "criteria"
    next_step = "publish"
    form_class = QuestionInlineFormSet


class SolicitationPublishView(SolicitationStepMixin, UpdateView):
    """Reviews the solicitation, then publishes it now or schedules it to go live, once nothing blocks it.

    A scheduled solicitation can't be edited, so this page is where it's published now, rescheduled or moved back
    to draft.
    """

    step = "publish"
    previous_step = "questions"
    form_class = SolicitationPublishForm
    template_name = "solicitation/solicitation_publish.html"

    def get_queryset(self):
        return (
            (super().get_queryset() | Solicitation.objects.scheduled().filter(organization=self.request.org))
            .select_related("currency", "country", "delivery_type", "program")
            .prefetch_related(
                Prefetch("criteria", queryset=SolicitationEvaluationCriterion.objects.order_by("pk")),
                Prefetch(
                    "questions", queryset=SolicitationQuestion.objects.select_related("criterion").order_by("pk")
                ),
            )
        )

    def get_context_data(self, **kwargs):
        return super().get_context_data(errors=publish_errors(self.object), **kwargs)

    def form_valid(self, form):
        form.instance.modified_by = self.request.user.email
        if form.instance.go_live_date:
            form.instance.status = Solicitation.Status.SCHEDULED
            messages.success(self.request, _("Solicitation scheduled to go live."))
        else:
            form.instance.status = Solicitation.Status.ACTIVE
            messages.success(self.request, _("Solicitation published."))
        return super().form_valid(form)

    def get_success_url(self):
        # Only invited organizations can apply to a private solicitation, so inviting them is the next step.
        if not self.object.public:
            return reverse("solicitation:invitations", args=[self.request.org.slug, self.object.solicitation_id])
        return super().get_success_url()


class SolicitationUnscheduleView(
    SolicitationStatusChangeMixin, EditableSolicitationMixin, SuccessMessageMixin, UpdateView
):
    """Takes a scheduled solicitation back to draft before it goes live, so it can be edited again."""

    status = Solicitation.Status.DRAFT
    fields = []
    http_method_names = ["post"]
    success_message = _("Solicitation moved back to draft.")

    def get_queryset(self):
        return Solicitation.objects.scheduled().filter(organization=self.request.org)

    def form_valid(self, form):
        form.instance.go_live_date = None
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("solicitation:details", args=[self.request.org.slug, self.object.solicitation_id])


class BaseSolicitationClosureView(
    SolicitationStatusChangeMixin, EditableSolicitationMixin, HtmxModalFormMixin, SuccessMessageMixin, UpdateView
):
    """Ends a solicitation with a reason, after which it can't be edited or applied to.

    The form is the modal on the list page.
    """

    form_class = SolicitationClosureForm
    template_name = "solicitation/closure_form.html"
    title = None
    hint = None

    def get_success_url(self):
        return reverse("solicitation:list", args=[self.request.org.slug])


class SolicitationCloseView(BaseSolicitationClosureView):
    status = Solicitation.Status.CLOSED
    title = _("Close solicitation")
    hint = _("Marks the solicitation closed before its deadline. It can't be edited or reopened afterwards.")
    success_message = _("Solicitation closed.")

    def get_queryset(self):
        # A draft was never open to applications, so there is nothing to close.
        return super().get_queryset().open()


class SolicitationCancelView(BaseSolicitationClosureView):
    status = Solicitation.Status.CANCELLED
    title = _("Cancel solicitation")
    hint = _("Calls the solicitation off. A cancelled solicitation can't be edited, applied to or reopened.")
    success_message = _("Solicitation cancelled.")


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


class SolicitationParticipantsView(SolicitationParticipantsMixin, SingleTableMixin, FormView):
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
        solicitation_crumb = {"title": self.solicitation.title}
        # The details page only exists while the solicitation can be edited.
        if self.solicitation.is_editable:
            solicitation_crumb["url"] = self.solicitation_url("details")
        path = [
            {"title": _("Solicitations"), "url": reverse("solicitation:list", args=[self.request.org.slug])},
            solicitation_crumb,
            {"title": self.title, "url": self.request.path},
        ]
        return super().get_context_data(
            solicitation=self.solicitation,
            path=path,
            delete_url=self.solicitation_url(f"delete_{self.url_name}"),
            **kwargs,
        )

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
