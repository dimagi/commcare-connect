"""Applying to a solicitation as one of the user's organizations."""

from functools import cached_property
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.generic import TemplateView
from waffle.mixins import WaffleSwitchMixin

from commcare_connect.flags.switch_names import SOLICITATIONS
from commcare_connect.solicitation.forms import MAX_ANSWER_FILE_SIZE_MB, ApplicationAnswersForm
from commcare_connect.solicitation.models import Solicitation, SolicitationApplication
from commcare_connect.solicitation.services import ApplicationNotEditable, save_application
from commcare_connect.users.helpers import CURRENT_ORG_SESSION_KEY


class SolicitationApplyView(WaffleSwitchMixin, LoginRequiredMixin, TemplateView):
    """Picks which organization to apply as, then answers the solicitation's questions.

    Only admins of an organization can apply for it. Saving a draft keeps the answers without checking them; submitting
    checks the required ones, sends the application and emails the submitter. A submitted application can't be
    changed here. Once the solicitation closes, the page says so instead of showing the form.
    """

    waffle_switch = SOLICITATIONS
    template_name = "solicitation/apply.html"

    @cached_property
    def solicitation(self):
        # Closed ones are still found, so someone part-way through the form is told it closed rather than shown a 404.
        return get_object_or_404(
            Solicitation.objects.visible_to(self.request.user)
            .exclude(status=Solicitation.Status.DRAFT)
            .select_related("organization", "currency", "country"),
            solicitation_id=self.kwargs["solicitation_id"],
        )

    @cached_property
    def organizations(self):
        return list(self.solicitation.applicant_organizations(self.request.user).order_by("name"))

    @cached_property
    def organization(self):
        """The organization picked in the query string, or the only one the user can apply as.

        None when the query string names an organization the user can't apply as, such as one they're no longer an
        admin of; the page then asks them to pick.
        """
        slug = self.request.GET.get("organization")
        if slug is None:
            return self.organizations[0] if len(self.organizations) == 1 else None
        return next((organization for organization in self.organizations if organization.slug == slug), None)

    @cached_property
    def application(self):
        if self.organization is None:
            return None
        return SolicitationApplication.objects.filter(
            solicitation=self.solicitation, organization=self.organization
        ).first() or SolicitationApplication(solicitation=self.solicitation, organization=self.organization)

    @property
    def choosing_organization(self):
        """The user can apply as several organizations and hasn't said which yet."""
        return self.solicitation.is_open and bool(self.organizations) and self.organization is None

    @property
    def editable(self):
        return (
            self.solicitation.is_open
            and self.application is not None
            and self.application.status == SolicitationApplication.Status.DRAFT
        )

    def get(self, request, *args, **kwargs):
        if self.organization is not None:
            # Applying as an organization makes it the one the user is working in, as the header shows.
            request.session[CURRENT_ORG_SESSION_KEY] = self.organization.slug
        return super().get(request, *args, **kwargs)

    def get_template_names(self):
        if self.choosing_organization:
            return ["account/select_organization.html"]
        return [self.template_name]

    def get_context_data(self, **kwargs):
        if self.editable and "form" not in kwargs:
            kwargs["form"] = ApplicationAnswersForm(application=self.application)
        path = [
            {"title": _("Marketplace"), "url": reverse("marketplace:list")},
            {
                "title": self.solicitation.title,
                "url": reverse("marketplace:detail", args=[self.solicitation.solicitation_id]),
            },
            {"title": _("Apply"), "url": self.request.path},
        ]
        create_org_url = f"{reverse('organization_create')}?{urlencode({'next': self.request.path})}"
        return super().get_context_data(
            solicitation=self.solicitation,
            organizations=self.organizations,
            organization=self.organization,
            application=self.application,
            closed=not self.solicitation.is_open,
            max_file_size_mb=MAX_ANSWER_FILE_SIZE_MB,
            path=path,
            create_org_url=create_org_url,
            heading=_("Choose the organization you're applying as"),
            description=_("You're applying to %(title)s. Pick the organization this application is from.")
            % {"title": self.solicitation.title},
            **kwargs,
        )

    def post(self, request, *args, **kwargs):
        if not self.solicitation.is_open:
            # The deadline passed while they were filling in the form: the page explains it closed.
            return self.render_to_response(self.get_context_data())
        if self.organization is None or not self.editable:
            return HttpResponseBadRequest()
        submitting = "submit" in request.POST
        form = ApplicationAnswersForm(
            request.POST, request.FILES, application=self.application, require_answers=submitting
        )
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))
        try:
            application = save_application(
                solicitation=self.solicitation,
                organization=self.organization,
                answers=form,
                user=request.user,
                submit=submitting,
            )
        except ApplicationNotEditable:
            return HttpResponseBadRequest()
        if submitting:
            messages.success(request, _("Application submitted. We've emailed you a confirmation."))
        else:
            messages.success(request, _("Draft saved. You can come back and finish it before the deadline."))
        return redirect(application.applicant_url)
