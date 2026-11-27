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
from django.views import View
from django.views.generic import DetailView, FormView, UpdateView
from django_filters.views import FilterView
from django_tables2 import SingleTableMixin, SingleTableView
from waffle.mixins import WaffleSwitchMixin

from commcare_connect.flags.switch_names import SOLICITATIONS
from commcare_connect.organization.decorators import org_pm_required
from commcare_connect.solicitation.filters import MarketplaceFilterSet, ReceivedApplicationFilterSet
from commcare_connect.solicitation.forms import (
    CriterionInlineFormSet,
    QuestionInlineFormSet,
    SolicitationClosureForm,
    SolicitationForm,
    SolicitationInvitationDeleteForm,
    SolicitationInvitationForm,
    publish_errors,
)
from commcare_connect.solicitation.models import (
    Solicitation,
    SolicitationApplication,
    SolicitationAttachment,
    SolicitationEvaluationCriterion,
    SolicitationInvitation,
    SolicitationQuestion,
)
from commcare_connect.solicitation.tables import (
    SolicitationApplicationTable,
    SolicitationInvitationTable,
    SolicitationTable,
)
from commcare_connect.solicitation.tasks import send_solicitation_invite_emails
from commcare_connect.utils.file import stored_file_response
from commcare_connect.utils.tables import get_validated_page_size
from commcare_connect.utils.views import HtmxModalFormMixin

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
            # distinct, since counting two relations joins both and would multiply each count by the other.
            .annotate(
                response_count=Count(
                    "applications",
                    filter=~Q(applications__status__in=SolicitationApplication.NON_RESPONSE_STATUSES),
                    distinct=True,
                ),
                invited_count=Count("invitations", distinct=True),
            )
            .order_by("-date_created")
        )


class FiltersAppliedMixin:
    """Adds `filters_applied` to a FilterView's context, so the page can offer to clear them."""

    def get_context_data(self, **kwargs):
        return super().get_context_data(filters_applied=self._filters_applied(), **kwargs)

    def _filters_applied(self):
        """Whether the filters narrow the list, or were refused (which empties it), going by what they cleaned to
        rather than the query string: a custom range with no dates, say, narrows nothing."""
        form = self.filterset.form
        return form.is_bound and (not form.is_valid() or any(form.cleaned_data.values()))


class SolicitationPageMixin:
    """A page about one existing solicitation. Subclasses set title and a `solicitation` property."""

    title = None

    def get_context_data(self, **kwargs):
        org_slug = self.request.org.slug
        details_url = None  # closed and cancelled solicitations have no details page to link to
        if self.solicitation.is_editable:
            details_url = reverse("solicitation:details", args=[org_slug, self.solicitation.solicitation_id])
        path = [
            {"title": _("My solicitations"), "url": reverse("solicitation:list", args=[org_slug])},
            {"title": self.solicitation.title, "url": details_url},
            {"title": self.title, "url": self.request.path},
        ]
        return super().get_context_data(solicitation=self.solicitation, path=path, **kwargs)


@method_decorator(org_pm_required, name="dispatch")
class EditableSolicitationMixin(WaffleSwitchMixin):
    """Looks up one of the org's solicitations that can still change; closed and cancelled ones are not found."""

    waffle_switch = SOLICITATIONS
    slug_field = "solicitation_id"
    slug_url_kwarg = "solicitation_id"

    def get_queryset(self):
        return Solicitation.objects.editable().filter(organization=self.request.org)


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
            {"title": _("My solicitations"), "url": reverse("solicitation:list", args=[self.request.org.slug])},
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


class SolicitationPublishView(SolicitationStepMixin, DetailView):
    """Reviews the solicitation and publishes it once nothing blocks it."""

    step = "publish"
    previous_step = "questions"
    template_name = "solicitation/solicitation_publish.html"

    def get_queryset(self):
        return (
            super()
            .get_queryset()
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

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        if self.object.structure_locked or publish_errors(self.object):
            return self.render_to_response(self.get_context_data())
        self.object.status = Solicitation.Status.ACTIVE
        self.object.modified_by = request.user.email
        self.object.save(update_fields=["status", "modified_by", "date_modified"])
        messages.success(request, _("Solicitation published."))
        return redirect(self.get_success_url())

    def get_success_url(self):
        # Only invited organizations can apply to a private solicitation, so inviting them is the next step.
        if not self.object.public:
            return reverse("solicitation:invitations", args=[self.request.org.slug, self.object.solicitation_id])
        return super().get_success_url()


class BaseSolicitationClosureView(EditableSolicitationMixin, HtmxModalFormMixin, SuccessMessageMixin, UpdateView):
    """Ends a solicitation with a reason, after which it can't be edited or applied to.

    The form is the modal on the list page.
    """

    form_class = SolicitationClosureForm
    template_name = "solicitation/closure_form.html"
    status = None
    title = None
    hint = None

    def form_valid(self, form):
        form.instance.status = self.status
        form.instance.modified_by = self.request.user.email
        return super().form_valid(form)

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


@method_decorator(org_pm_required, name="dispatch")
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


# Public marketplace ---------------------------------------------------------------------------------------------

CARD_RELATED = ("organization", "country", "delivery_type")


class MarketplaceListView(WaffleSwitchMixin, FiltersAppliedMixin, FilterView):
    """Every solicitation an organization can apply to. Not scoped to an organization and, unlike every other
    view here, open to visitors who aren't logged in."""

    waffle_switch = SOLICITATIONS
    filterset_class = MarketplaceFilterSet
    template_name = "solicitation/marketplace_list.html"
    context_object_name = "solicitations"
    paginate_by = 12  # fills whole rows of the 1, 2 and 3 column grid

    def get_queryset(self):
        # Soonest deadline first.
        return Solicitation.objects.public().select_related(*CARD_RELATED).order_by("application_deadline", "pk")


class MarketplaceDetailView(WaffleSwitchMixin, DetailView):
    """One open solicitation's scope, budget and deadline, without its questions or criteria. Private ones are
    only found by members of the organization that posted them and of the organizations they invited."""

    waffle_switch = SOLICITATIONS
    template_name = "solicitation/marketplace_detail.html"
    context_object_name = "solicitation"
    slug_field = "solicitation_id"
    slug_url_kwarg = "solicitation_id"

    def get_queryset(self):
        return (
            Solicitation.objects.open()
            .visible_to(self.request.user)
            .select_related(*CARD_RELATED)
            .prefetch_related("attachments")
        )

    def get_context_data(self, **kwargs):
        path = [
            {"title": _("Marketplace"), "url": reverse("marketplace:list")},
            {"title": self.object.title, "url": self.request.path},
        ]
        return super().get_context_data(path=path, **kwargs)


class MarketplaceAttachmentView(WaffleSwitchMixin, View):
    """Downloads one of an open solicitation's attachments, for whoever can see its details."""

    waffle_switch = SOLICITATIONS

    def get(self, request, solicitation_id, attachment_id):
        attachment = get_object_or_404(
            SolicitationAttachment,
            pk=attachment_id,
            solicitation__in=Solicitation.objects.open().visible_to(request.user),
            solicitation__solicitation_id=solicitation_id,
        )
        return stored_file_response(attachment.file, filename=attachment.filename)
