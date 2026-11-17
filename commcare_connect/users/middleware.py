from django.http import Http404
from django.shortcuts import render
from django.utils.deprecation import MiddlewareMixin
from django.utils.functional import SimpleLazyObject

from ..opportunity.models import Opportunity
from ..organization.models import Organization
from ..organization.models import UserOrganizationMembership as Membership
from ..program.models import Program
from ..program.utils import (
    AccessLevel,
    is_opportunity_pm,
    opportunity_access_level_from_request,
    opportunity_by_id,
    org_access_level_from_request,
)
from ..utils.db import get_object_by_uuid_or_int
from .helpers import active_memberships, get_organization_for_request


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
        memberships = active_memberships(request.user)
        request._cached_memberships = memberships
    return request._cached_memberships


def _is_opportunity_pm(request, view_kwargs):
    opp_id = view_kwargs.get("opp_id", None)

    if not opp_id:
        return None

    if not hasattr(request, "_cached_opportunity_pm"):
        opportunity = getattr(request, "opportunity", None) or opportunity_by_id(opp_id)
        request._cached_opportunity_pm = is_opportunity_pm(request, opportunity)
    return request._cached_opportunity_pm


def _opportunity_access_level(request, view_kwargs):
    opp_id = view_kwargs.get("opp_id", None)

    if not opp_id:
        return AccessLevel.NONE

    if not hasattr(request, "_cached_opp_access_level"):
        opportunity = getattr(request, "opportunity", None) or opportunity_by_id(opp_id)
        request._cached_opp_access_level = opportunity_access_level_from_request(request, opportunity)
    return request._cached_opp_access_level


class OrganizationMiddleware(MiddlewareMixin):
    def process_view(self, request, view_func, view_args, view_kwargs):
        request.org = SimpleLazyObject(lambda: _get_organization(request, view_kwargs))
        request.org_membership = SimpleLazyObject(lambda: _get_org_membership(request))
        request.memberships = SimpleLazyObject(lambda: _get_all_memberships(request))
        request.is_opportunity_pm = SimpleLazyObject(lambda: _is_opportunity_pm(request, view_kwargs))
        request.org_access_level = SimpleLazyObject(lambda: org_access_level_from_request(request))
        request.opp_access_level = SimpleLazyObject(lambda: _opportunity_access_level(request, view_kwargs))
        if _is_hidden_with_archived_organization(request, view_kwargs):
            request.org = None
            return _render_organization_archived(request)


def _is_hidden_with_archived_organization(request, view_kwargs):
    """Whether the page belongs to an archived organization, or to a program or opportunity owned by one."""
    org_slug = view_kwargs.get("org_slug")
    if org_slug is None or not request.path.startswith(f"/a/{org_slug}/"):
        return False
    if Organization.objects.filter(slug=org_slug, is_archived=True).exists():
        return True
    opp_id = view_kwargs.get("opp_id")
    if opp_id is not None and _owned_by_archived_organization(
        Opportunity.objects.filter(program__organization__is_archived=True), opp_id, "opportunity_id"
    ):
        return True
    program_id = view_kwargs.get("pk")
    return (
        program_id is not None
        and request.resolver_match.namespace == "program"
        and _owned_by_archived_organization(
            Program.objects.filter(organization__is_archived=True), program_id, "program_id"
        )
    )


def _owned_by_archived_organization(queryset, lookup_value, uuid_field):
    try:
        get_object_by_uuid_or_int(queryset, str(lookup_value), uuid_field=uuid_field)
    except Http404:
        return False
    return True


def _render_organization_archived(request):
    authenticated = request.user.is_authenticated
    context = {
        "has_active_organization": authenticated and active_memberships(request.user).exists(),
        "can_create_organization": authenticated,
    }
    return render(request, "organization/organization_archived.html", context, status=404)
