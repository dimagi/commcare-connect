"""The public marketplace, open to visitors who aren't logged in."""

from urllib.parse import urlencode

from django.db.models import Prefetch
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import DetailView
from django_filters.views import FilterView
from waffle.mixins import WaffleSwitchMixin

from commcare_connect.flags.switch_names import SOLICITATIONS
from commcare_connect.solicitation.filters import MarketplaceFilterSet
from commcare_connect.solicitation.models import Solicitation, SolicitationApplication, SolicitationAttachment
from commcare_connect.utils.file import stored_file_response

# What a marketplace card shows besides the solicitation itself.
CARD_RELATED = ("organization", "country", "delivery_type")


class MarketplaceListView(WaffleSwitchMixin, FilterView):
    """Every solicitation an organization can apply to. Not scoped to an organization and, unlike every other
    view here, open to visitors who aren't logged in.
    """

    waffle_switch = SOLICITATIONS
    filterset_class = MarketplaceFilterSet
    template_name = "solicitation/marketplace_list.html"
    context_object_name = "solicitations"
    paginate_by = 12  # fills whole rows of the 1, 2 and 3 column grid

    def get_queryset(self):
        return self._with_own_application(_as_cards(Solicitation.objects.public()))

    def get_context_data(self, **kwargs):
        return super().get_context_data(filters_applied=self._filters_applied(), **kwargs)

    def _with_own_application(self, solicitations):
        """Gives each solicitation `own_applications`: the current organization's application to it, if any."""
        if not self.request.user.is_authenticated or not self.request.org:
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

    def _filters_applied(self):
        """Whether the filters narrow the list, or were refused (which empties it), going by what they cleaned to
        rather than the query string: a custom range with no dates, say, narrows nothing."""
        form = self.filterset.form
        if not form.is_bound:  # no query string at all
            return False
        return not form.is_valid() or any(form.cleaned_data.values())


class MarketplaceDetailView(WaffleSwitchMixin, DetailView):
    """One open solicitation's scope, budget and deadline, without its questions or criteria. Private ones are
    only found by members of the organizations they invited."""

    waffle_switch = SOLICITATIONS
    template_name = "solicitation/marketplace_detail.html"
    context_object_name = "solicitation"
    slug_field = "solicitation_id"
    slug_url_kwarg = "solicitation_id"

    def get_queryset(self):
        return (
            Solicitation.objects.open()
            .visible_to(self.request.user)
            .select_related(*CARD_RELATED, "currency")
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
        user applies as the organization they are in when it has no application yet, and otherwise picks one there.
        An organization with a draft reaches it through its application page.
        """
        apply_url = reverse("marketplace:apply", args=[self.object.solicitation_id])
        user = self.request.user
        if not user.is_authenticated:
            return {"applications": [], "apply_url": apply_url}
        applications = list(
            self.object.applications.filter(organization__memberships__user=user)
            .select_related("organization")
            .order_by("organization__name")
        )
        still_open = self.object.applicant_organizations(user).exclude(
            pk__in=[application.organization_id for application in applications]
        )
        if self.request.org and still_open.filter(pk=self.request.org.pk).exists():
            apply_url = f"{apply_url}?{urlencode({'organization': self.request.org.slug})}"
        elif applications and not still_open.exists():
            apply_url = None
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


def _as_cards(solicitations):
    """Soonest deadline first, with what each card shows."""
    return solicitations.select_related(*CARD_RELATED).order_by("application_deadline", "pk")
