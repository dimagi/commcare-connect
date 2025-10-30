from functools import wraps

from django.http import Http404, HttpResponseRedirect
from django.urls import reverse
from django.utils.decorators import method_decorator

from commcare_connect.utils.permission_const import ALL_ORG_ACCESS

from .models import UserOrganizationMembership


def _request_user_is_member(request):
    return (request.org and request.org_membership and not request.org_membership.is_viewer) or request.user.has_perm(
        ALL_ORG_ACCESS
    )


def _request_user_is_admin(request):
    return (
        request.org and request.org_membership and request.org_membership.role == UserOrganizationMembership.Role.ADMIN
    ) or request.user.has_perm(ALL_ORG_ACCESS)


def _request_user_is_program_manager(request):
    return (
        request.org and request.org_membership and request.org_membership.is_admin and request.org.program_manager
    ) or request.user.has_perm(ALL_ORG_ACCESS)


def _request_user_is_viewer(request):
    return (request.org and request.org_membership) or request.user.has_perm(ALL_ORG_ACCESS)


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
            raise Http404()

        return view_func(request, *args, **kwargs)

    return _inner


class OrganizationUserMixin:
    """Mixin version of org_viewer_required decorator"""

    @method_decorator(org_viewer_required)
    def dispatch(self, request, *args, **kwargs):
        return super().dispatch(request, *args, **kwargs)


class OrganizationUserMemberRoleMixin:
    """Mixin version of org_member_required decorator"""

    @method_decorator(org_member_required)
    def dispatch(self, request, *args, **kwargs):
        return super().dispatch(request, *args, **kwargs)
