from urllib.parse import urlencode

from django.urls import reverse

from commcare_connect.opportunity.models import OpportunityStatus

# Filters the opportunity list opens with: active, non-test opportunities. They live in the URL rather
# than the view so a bare list URL means "no filters" and clearing them in the filter modal shows everything.
DEFAULT_OPPORTUNITY_LIST_FILTERS = {"status": OpportunityStatus.ACTIVE.value, "is_test": False}


def opportunity_list_url(org_slug, **extra_params):
    """URL of the opportunity list with the default filters applied, plus any extra query params."""
    query = urlencode({**DEFAULT_OPPORTUNITY_LIST_FILTERS, **extra_params})
    return f"{reverse('opportunity:list', args=(org_slug,))}?{query}"
