"""The public marketplace, open to visitors who aren't logged in."""

from django_filters.views import FilterView
from waffle.mixins import WaffleSwitchMixin

from commcare_connect.flags.switch_names import SOLICITATIONS
from commcare_connect.solicitation.filters import MarketplaceFilterSet
from commcare_connect.solicitation.models import Solicitation


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
        return (
            Solicitation.objects.public()
            .select_related("organization", "country", "delivery_type")
            .order_by("application_deadline", "pk")
        )

    def get_context_data(self, **kwargs):
        return super().get_context_data(filters_applied=self._filters_applied(), **kwargs)

    def _filters_applied(self):
        """Whether the filters narrow the list, or were refused (which empties it), going by what they cleaned to
        rather than the query string: a custom range with no dates, say, narrows nothing."""
        form = self.filterset.form
        if not form.is_bound:  # no query string at all
            return False
        return not form.is_valid() or any(form.cleaned_data.values())
