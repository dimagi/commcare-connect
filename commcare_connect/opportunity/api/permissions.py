from rest_framework.permissions import IsAuthenticated

from commcare_connect.organization.models import UserOrganizationMembership


class IsOrgProgramManagerAdmin(IsAuthenticated):
    """
    Allows access only to authenticated users who are admins of a
    program_manager organization. The organization is determined from:
    1. URL kwargs ('org_slug')
    2. Request data ('organization' field as slug)
    3. Request query params ('organization')
    """

    def _get_org_slug(self, request, view):
        org_slug = view.kwargs.get("org_slug")
        if org_slug:
            return org_slug
        return request.data.get("organization") or request.query_params.get("organization")

    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False

        org_slug = self._get_org_slug(request, view)
        if not org_slug:
            return False

        try:
            membership = UserOrganizationMembership.objects.select_related("organization").get(
                user=request.user,
                organization__slug=org_slug,
            )
        except UserOrganizationMembership.DoesNotExist:
            return False

        return membership.is_admin and membership.organization.program_manager
