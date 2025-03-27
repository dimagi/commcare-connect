from django import template

from commcare_connect.cache import quickcache
from commcare_connect.opportunity.models import Opportunity
from commcare_connect.program.models import ManagedOpportunity, Program

register = template.Library()


@register.filter
def can_user_manage_opportunity(request, opportunity):
    if request.user_is_superuser:
        return True

    current_org = getattr(request, "org", None)
    org_membership = getattr(request, "org_membership", None)

    is_admin = bool(current_org and org_membership and org_membership.is_admin)

    return is_admin and is_program_manager_of_opportunity(current_org.slug, opportunity)


@quickcache(vary_on=["current_org_slug", "opportunity.id"], timeout=24 * 60 * 60)
def is_program_manager_of_opportunity(current_org_slug, opportunity: Opportunity):
    """
    Check if the current organization manages the given managed opportunity
    through any of its programs.
    """
    programs = Program.objects.filter(organization__slug=current_org_slug)
    if programs:
        return ManagedOpportunity.objects.filter(program__in=programs, opportunity=opportunity).exists()

    return False
