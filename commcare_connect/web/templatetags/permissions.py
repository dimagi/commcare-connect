from django import template

from commcare_connect.program.helpers import is_org_manages_opportunity_via_program

register = template.Library()


@register.filter
def is_program_manager_for_opportunity(request, opp_id):
    if request.user.is_superuser:
        return True

    current_org = getattr(request, "org", None)
    org_membership = getattr(request, "org_membership", None)

    is_admin = bool(current_org and org_membership and org_membership.is_admin)

    return (
        current_org.program_manager and is_admin and is_org_manages_opportunity_via_program(current_org.slug, opp_id)
    )
