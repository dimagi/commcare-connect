from typing import Any

from django import template
from django.http import HttpRequest

from commcare_connect.opportunity.models import Opportunity
from commcare_connect.organization.decorators import is_user_pm_org_admin
from commcare_connect.program.utils import (
    AccessLevel,
    opportunity_access_level_from_request,
    program_access_level_from_request,
)

register = template.Library()

register.filter("is_pm_org_admin", is_user_pm_org_admin)


@register.simple_tag(takes_context=True)
def page_context(context: template.Context) -> dict[str, Any]:
    """The program and opportunity the current page is about, for the header's context selector.

    Views attach these to the request before checking access, so each is returned only when
    the user may see it. Otherwise an error page could show a name the user isn't allowed to see.
    """
    request = context.get("request")
    opportunity = getattr(request, "opportunity", None)
    if opportunity is not None and _opportunity_access_level(request, opportunity).has_view:
        return {"program": opportunity.program, "opportunity": opportunity}

    program = getattr(request, "program", None)
    if program is not None and program_access_level_from_request(request, program).has_view:
        return {"program": program, "opportunity": None}
    return {"program": None, "opportunity": None}


def _opportunity_access_level(request: HttpRequest, opportunity: Opportunity) -> AccessLevel:
    """Reuse the access level the view already worked out. The middleware sets it only for URLs with an opp_id."""
    if request.resolver_match and request.resolver_match.kwargs.get("opp_id"):
        return request.opp_access_level
    return opportunity_access_level_from_request(request, opportunity)
