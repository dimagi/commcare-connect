from django.utils.deprecation import MiddlewareMixin
from django.utils.functional import SimpleLazyObject

from ..organization.models import UserOrganizationMembership as Membership
from ..program.utils import is_program_manager_of_opportunity
from .helpers import get_organization_for_request


def _get_organization(request, view_kwargs):
    if not hasattr(request, "_cached_org"):
        team = get_organization_for_request(request, view_kwargs)
        request._cached_org = team
    return request._cached_org


def _get_org_membership(request):
    if not hasattr(request, "_cached_org_membership"):
        org = request.org
        membership = None
        if org:
            try:
                membership = Membership.objects.get(organization=org, user=request.user) if org else None
            except Membership.DoesNotExist:
                pass
        request._cached_org_membership = membership
    return request._cached_org_membership


def _get_all_memberships(request):
    if not hasattr(request, "_cached_memberships"):
        memberships = Membership.objects.filter(user=request.user)
        request._cached_memberships = memberships
    return request._cached_memberships


def _is_opportunity_pm(request, view_kwargs):
    opp_id = view_kwargs.get("opp_id", None)

    if not opp_id:
        return None

    if not hasattr(request, "_cached_opportunity_pm"):
        request._cached_opportunity_pm = is_program_manager_of_opportunity(request, opp_id)
    return request._cached_opportunity_pm


class OrganizationMiddleware(MiddlewareMixin):
    def process_view(self, request, view_func, view_args, view_kwargs):
        request.org = SimpleLazyObject(lambda: _get_organization(request, view_kwargs))
        request.org_membership = SimpleLazyObject(lambda: _get_org_membership(request))
        request.memberships = SimpleLazyObject(lambda: _get_all_memberships(request))
        request.is_opportunity_pm = SimpleLazyObject(lambda: _is_opportunity_pm(request, view_kwargs))
