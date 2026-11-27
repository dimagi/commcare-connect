"""Who may post and run solicitations."""

from functools import wraps

from django.http import Http404

from commcare_connect.organization.decorators import is_user_pm_org_admin, org_is_program_manager, org_pm_required


def can_manage_solicitations(user, organization):
    """Admins of program manager orgs post solicitations. Staff with ALL_ORG_ACCESS can act for any org, but other
    orgs don't post solicitations, so they can only do so in program manager orgs too."""
    return org_is_program_manager(organization) and is_user_pm_org_admin(user, organization)


def solicitation_manager_required(view_func):
    """org_pm_required, limited to program manager orgs even for staff."""

    @wraps(view_func)
    def _inner(request, *args, **kwargs):
        if not org_is_program_manager(request.org):
            raise Http404()
        return view_func(request, *args, **kwargs)

    return org_pm_required(_inner)
