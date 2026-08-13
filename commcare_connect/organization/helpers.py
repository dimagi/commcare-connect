from commcare_connect.organization.models import Organization
from commcare_connect.utils.permission_const import WORKSPACE_ENTITY_MANAGEMENT_ACCESS


def orgs_visible_to(user):
    """Organizations the user may select on the workspace create/switch form.

    Users with workspace entity management access administer every workspace, so they
    see all of them. Everyone else only sees the workspaces they belong to, which is
    nothing at all for a user who has just signed up.
    """
    if user.has_perm(WORKSPACE_ENTITY_MANAGEMENT_ACCESS):
        return Organization.objects.all()
    return Organization.objects.filter(memberships__user=user)
