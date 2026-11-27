"""The public marketplace: open solicitations, not scoped to an organization."""

from django.db.models import Prefetch
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import DetailView
from django_filters.views import FilterView
from waffle.mixins import WaffleSwitchMixin

from commcare_connect.flags.switch_names import SOLICITATIONS
from commcare_connect.organization.models import UserOrganizationMembership
from commcare_connect.solicitation.filters import MarketplaceFilterSet
from commcare_connect.solicitation.models import Solicitation, SolicitationApplication, SolicitationAttachment
from commcare_connect.solicitation.views.management import FiltersAppliedMixin
from commcare_connect.utils.file import stored_file_response

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
        solicitations = (
            Solicitation.objects.public().select_related(*CARD_RELATED).order_by("application_deadline", "pk")
        )
        return self._with_own_application(solicitations)

    def _with_own_application(self, solicitations):
        """Gives each solicitation `own_applications`: the current organization's application to it, if any.

        Only for its admins, since only they can open the application.
        """
        if not (self.request.org_membership and self.request.org_membership.is_admin):
            return solicitations
        return solicitations.prefetch_related(
            Prefetch(
                "applications",
                queryset=SolicitationApplication.objects.filter(organization=self.request.org).select_related(
                    "organization"
                ),
                to_attr="own_applications",
            )
        )


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
        return super().get_context_data(path=path, **self._applicant_context(), **kwargs)

    def _applicant_context(self):
        """The applications the user's organizations have started or sent, and where Apply leads, or None to hide it.

        Logged-out visitors and users who can't apply see Apply; the apply page explains what to do. A logged-in
        user applies as the organization they are in when it has no application yet. Once it has one, Apply is
        hidden; switching to another organization that can still apply brings it back. An organization with a
        draft reaches it through its application page.
        """
        user = self.request.user
        if not user.is_authenticated:
            return {"applications": [], "apply_url": self.object.apply_url()}
        applications = list(
            # Only admins can open an organization's application.
            self.object.applications.filter(
                organization__memberships__user=user,
                organization__memberships__role=UserOrganizationMembership.Role.ADMIN,
            )
            .select_related("organization")
            .order_by("organization__name")
        )
        still_open = list(
            self.object.applicant_organizations(user).exclude(
                pk__in=[application.organization_id for application in applications]
            )
        )
        current_org_applied = any(
            application.organization_id == getattr(self.request.org, "pk", None) for application in applications
        )
        if self.request.org in still_open:
            apply_url = self.object.apply_url(self.request.org)
        elif current_org_applied or (applications and not still_open):
            apply_url = None
        else:
            apply_url = self.object.apply_url()
        return {"applications": applications, "apply_url": apply_url}


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
