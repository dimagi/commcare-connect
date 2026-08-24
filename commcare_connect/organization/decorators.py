from functools import wraps

from django.http import Http404, HttpResponseRedirect
from django.urls import reverse
from django.utils.decorators import method_decorator
from rest_framework.permissions import BasePermission

from commcare_connect.opportunity.models import Opportunity
from commcare_connect.program.models import Program
from commcare_connect.program.utils import (
    AccessLevel,
    is_opportunity_nm,
    is_opportunity_pm,
    opportunity_access_level_from_request,
    opportunity_by_id,
    org_access_level_from_request,
    program_access_level_from_request,
)
from commcare_connect.utils.db import get_object_by_uuid_or_int
from commcare_connect.utils.permission_const import ALL_ORG_ACCESS

from .models import Organization, UserOrganizationMembership


def user_is_org_admin(user, organization):
    """Check if user is admin of the given org, or has ALL_ORG_ACCESS."""
    if user.has_perm(ALL_ORG_ACCESS):
        return True
    return UserOrganizationMembership.objects.filter(
        user=user,
        organization=organization,
        role=UserOrganizationMembership.Role.ADMIN,
    ).exists()


def user_is_org_pm(user, organization):
    """Creating programs takes two things: an org that is a program manager, and a caller who is its admin."""
    if user.has_perm(ALL_ORG_ACCESS):
        return True
    return org_is_program_manager(organization) and user_is_org_admin(user, organization)


def org_is_program_manager(organization):
    return bool(organization and organization.program_manager)


def user_is_opportunity_admin(user, opportunity):
    if user.has_perm(ALL_ORG_ACCESS):
        return True
    return UserOrganizationMembership.objects.filter(
        user=user,
        organization_id__in=[opportunity.organization_id, opportunity.program.organization_id],
        role=UserOrganizationMembership.Role.ADMIN,
    ).exists()


def user_is_opportunity_pm(user, opportunity):
    if user.has_perm(ALL_ORG_ACCESS):
        return True
    return UserOrganizationMembership.objects.filter(
        user=user,
        organization_id=opportunity.program.organization_id,
        organization__program_manager=True,
        role=UserOrganizationMembership.Role.ADMIN,
    ).exists()


class IsProgramManagerOrgAdmin(BasePermission):
    """DRF twin of org_pm_required. There is no request.org here, so the acting org comes from the slug."""

    def has_permission(self, request, view):
        org_slug = request.data.get("organization") or view.kwargs.get("org_slug")
        if not org_slug:
            return False
        return user_is_org_pm(request.user, Organization.objects.filter(slug=org_slug).first())


def is_org_pm_or_all_access(request, *args, **kwargs):
    """Same rule as user_is_org_pm."""
    if request.user.has_perm(ALL_ORG_ACCESS):
        return True
    membership = request.org_membership
    return org_is_program_manager(request.org) and bool(membership and membership.is_admin)


def opp_view_access_required(view_func):
    return _opportunity_access_level_gate(AccessLevel.VIEW)(view_func)


# TODO: These 3 are widely used in the opportunity app, and we are renaming it to opportunity access decorators
# in this commit for easier review. The next commit points at them at opportunity_access_level_from_request.
def opp_standard_access_required(view_func):
    return _opportunity_access_level_gate(AccessLevel.STANDARD)(view_func)


def opp_manage_access_required(view_func):
    return _opportunity_access_level_gate(AccessLevel.MANAGE)(view_func)


def org_pm_required(view_func, *args, **kwargs):
    return _get_decorated_function(view_func, is_org_pm_or_all_access)


def _program_access_level_gate(minimum, program_id_kwarg="pk"):
    def decorator(view_func):
        def has_required_access(request, *args, **kwargs):
            program_id = kwargs.get(program_id_kwarg)
            program = Program.objects.filter(program_id=program_id).first()

            request.program = program
            return program_access_level_from_request(request, program) >= minimum

        return _get_decorated_function(view_func, has_required_access)

    return decorator


def _opportunity_gate(has_required_access, opp_id_kwarg="opp_id"):
    def decorator(view_func):
        def permission_test(request, *args, **kwargs):
            opp_id = kwargs.get(opp_id_kwarg)
            opportunity = opportunity_by_id(opp_id) if opp_id else None
            if opportunity:
                request.opportunity = opportunity
            return has_required_access(request, opportunity)

        return _get_decorated_function(view_func, permission_test)

    return decorator


def _opportunity_access_level_gate(minimum, opp_id_kwarg="opp_id"):
    return _opportunity_gate(
        lambda request, opportunity: opportunity_access_level_from_request(request, opportunity) >= minimum,
        opp_id_kwarg,
    )


def _org_access_level_gate(minimum):
    def decorator(view_func):
        def has_required_access(request, *args, **kwargs):
            return org_access_level_from_request(request) >= minimum

        return _get_decorated_function(view_func, has_required_access)

    return decorator


program_view_access_required = _program_access_level_gate(AccessLevel.VIEW)
program_standard_access_required = _program_access_level_gate(AccessLevel.STANDARD)
program_manage_access_required = _program_access_level_gate(AccessLevel.MANAGE)

org_view_access_required = _org_access_level_gate(AccessLevel.VIEW)
org_standard_access_required = _org_access_level_gate(AccessLevel.STANDARD)
org_manage_access_required = _org_access_level_gate(AccessLevel.MANAGE)

# The party gates ask which side of the opportunity the org sits on, not how much access it has.
opportunity_pm_required = _opportunity_gate(is_opportunity_pm)
opportunity_nm_required = _opportunity_gate(is_opportunity_nm)


def _get_decorated_function(view_func, permission_test_function):
    @wraps(view_func)
    def _inner(request, *args, **kwargs):
        user = request.user
        if not user.is_authenticated:
            return HttpResponseRedirect("{}?next={}".format(reverse("account_login"), request.path))

        if not permission_test_function(request, *args, **kwargs):
            raise Http404()

        return view_func(request, *args, **kwargs)

    return _inner


def opportunity_required(view_func):
    """Fetch the opportunity named by the URL and attach it to request.opportunity.

    Object lookup only. Who may reach it is the gate's question, and every view carrying
    this decorator has one.
    """

    @wraps(view_func)
    def _inner(request, org_slug, opp_id, *args, **kwargs):
        if not opp_id:
            raise Http404("Opportunity ID not provided.")

        if not org_slug:
            raise Http404("Organization slug not provided.")

        request.opportunity = get_object_by_uuid_or_int(Opportunity.objects.all(), opp_id, uuid_field="opportunity_id")
        return view_func(request, org_slug=org_slug, opp_id=opp_id, *args, **kwargs)

    _inner._has_opportunity_required_decorator = True
    return _inner


class OrgViewAccessMixin:
    """Mixin version of org_view_access_required decorator"""

    @method_decorator(org_view_access_required)
    def dispatch(self, request, *args, **kwargs):
        return super().dispatch(request, *args, **kwargs)


class OppViewAccessMixin:
    """Mixin version of opp_view_access_required."""

    @method_decorator(opp_view_access_required)
    def dispatch(self, request, *args, **kwargs):
        return super().dispatch(request, *args, **kwargs)


class OrgPMRequiredMixin:
    """Mixin version of org_pm_required decorator"""

    @method_decorator(org_pm_required)
    def dispatch(self, request, *args, **kwargs):
        return super().dispatch(request, *args, **kwargs)


class OppStandardAccessMixin:
    """Mixin version of opp_standard_access_required."""

    @method_decorator(opp_standard_access_required)
    def dispatch(self, request, *args, **kwargs):
        return super().dispatch(request, *args, **kwargs)


class ProgramManageAccessMixin:
    @method_decorator(program_manage_access_required)
    def dispatch(self, request, *args, **kwargs):
        return super().dispatch(request, *args, **kwargs)


class ProgramViewAccessMixin:
    @method_decorator(program_view_access_required)
    def dispatch(self, request, *args, **kwargs):
        return super().dispatch(request, *args, **kwargs)


class OrgManageAccessMixin:
    @method_decorator(org_manage_access_required)
    def dispatch(self, request, *args, **kwargs):
        return super().dispatch(request, *args, **kwargs)


class OppPMRequiredMixin:
    @method_decorator(opportunity_pm_required)
    def dispatch(self, request, *args, **kwargs):
        return super().dispatch(request, *args, **kwargs)
