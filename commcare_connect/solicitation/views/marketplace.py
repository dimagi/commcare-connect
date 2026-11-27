"""The public marketplace: open solicitations, not scoped to an organization."""

from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import DetailView
from django_filters.views import FilterView
from waffle.mixins import WaffleSwitchMixin

from commcare_connect.flags.switch_names import SOLICITATIONS
from commcare_connect.solicitation.filters import MarketplaceFilterSet
from commcare_connect.solicitation.models import Solicitation, SolicitationAttachment
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
