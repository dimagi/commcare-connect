from functools import wraps

from django.http import Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse

from commcare_connect.opportunity.models import Opportunity

from .models import UserOrganizationMembership


def _request_user_is_member(request):
    return (
        request.org and request.org_membership and not request.org_membership.is_viewer
    ) or request.user.is_superuser


def _request_user_is_admin(request):
    return (
        request.org and request.org_membership and request.org_membership.role == UserOrganizationMembership.Role.ADMIN
    ) or request.user.is_superuser


def _request_user_is_program_manager(request):
    return (
        request.org and request.org_membership and request.org_membership.is_admin and request.org.program_manager
    ) or request.user.is_superuser


def _request_user_is_viewer(request):
    return (request.org and request.org_membership) or request.user.is_superuser


def org_member_required(view_func):
    return _get_decorated_function(view_func, _request_user_is_member)


def org_admin_required(view_func):
    return _get_decorated_function(view_func, _request_user_is_admin)


def org_viewer_required(view_func):
    return _get_decorated_function(view_func, _request_user_is_viewer)


def org_program_manager_required(view_func):
    return _get_decorated_function(view_func, _request_user_is_program_manager)


def _get_decorated_function(view_func, permission_test_function):
    @wraps(view_func)
    def _inner(request, *args, **kwargs):
        user = request.user
        if not user.is_authenticated:
            return HttpResponseRedirect("{}?next={}".format(reverse("account_login"), request.path))

        if not permission_test_function(request):
            raise Http404

        return view_func(request, *args, **kwargs)

    return _inner


def opportunity_for_org_required(view_func):
    """
    Decorator that fetches the opportunity from URL parameters (opp_id and org_slug)
    and attaches it to request.opportunity. Raises Http404 if the opportunity doesn't
    exist or doesn't belong to the organization.

    This decorator should be used after org permission decorators to ensure request.org is available.
    """

    @wraps(view_func)
    def _inner(request, org_slug, opp_id, *args, **kwargs):
        if not opp_id:
            raise Http404("Opportunity ID not provided.")

        if not org_slug:
            raise Http404("Organization slug not provided.")

        opp = get_object_or_404(Opportunity, id=opp_id)

        if (opp.organization and opp.organization.slug == org_slug) or (
            opp.managed and opp.managedopportunity.program.organization.slug == org_slug
        ):
            request.opportunity = opp
            return view_func(request, org_slug, opp_id, *args, **kwargs)

        raise Http404("Opportunity not found.")

    _inner._has_opportunity_for_org_required_decorator = True
    return _inner
