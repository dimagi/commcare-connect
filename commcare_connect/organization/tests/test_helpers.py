import pytest
from django.contrib.auth.models import Permission

from commcare_connect.organization.helpers import orgs_visible_to
from commcare_connect.organization.models import Organization
from commcare_connect.users.models import User
from commcare_connect.users.tests.factories import MembershipFactory, OrganizationFactory, UserFactory


def _user_with_privilege(privilege: str | None, own_org: Organization) -> User:
    user = UserFactory(is_superuser=privilege == "superuser")
    if privilege == "member":
        MembershipFactory(user=user, organization=own_org)
    if privilege == "permission":
        user.user_permissions.add(Permission.objects.get(codename="workspace_entity_management_access"))
        user = User.objects.get(pk=user.pk)  # drop the cached permissions
    return user


@pytest.mark.django_db
@pytest.mark.parametrize(
    "privilege, visible",
    [
        (None, set()),
        ("member", {"own"}),
        ("permission", {"own", "other"}),
        ("superuser", {"own", "other"}),
    ],
)
def test_orgs_visible_to(privilege, visible):
    orgs = {"own": OrganizationFactory(), "other": OrganizationFactory()}
    user = _user_with_privilege(privilege, orgs["own"])

    assert set(orgs_visible_to(user)) == {orgs[key] for key in visible}
