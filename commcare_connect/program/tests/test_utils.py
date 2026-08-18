import pytest

from commcare_connect.organization.models import UserOrganizationMembership
from commcare_connect.program.utils import AccessLevel, org_program_access, user_org_access
from commcare_connect.utils.test_utils import make_membership

Role = UserOrganizationMembership.Role


class TestAccessLevel:
    def test_levels_are_ordered(self):
        assert AccessLevel.NONE < AccessLevel.VIEW < AccessLevel.STANDARD < AccessLevel.MANAGE

    @pytest.mark.parametrize(
        "org_level,user_level,expected",
        [
            (AccessLevel.MANAGE, AccessLevel.MANAGE, AccessLevel.MANAGE),
            (AccessLevel.MANAGE, AccessLevel.STANDARD, AccessLevel.STANDARD),
            (AccessLevel.MANAGE, AccessLevel.VIEW, AccessLevel.VIEW),
            (AccessLevel.MANAGE, AccessLevel.NONE, AccessLevel.NONE),
            (AccessLevel.STANDARD, AccessLevel.MANAGE, AccessLevel.STANDARD),
            (AccessLevel.VIEW, AccessLevel.MANAGE, AccessLevel.VIEW),
            (AccessLevel.VIEW, AccessLevel.STANDARD, AccessLevel.VIEW),
            (AccessLevel.VIEW, AccessLevel.VIEW, AccessLevel.VIEW),
            (AccessLevel.NONE, AccessLevel.MANAGE, AccessLevel.NONE),
        ],
    )
    def test_effective_takes_the_weaker_level(self, org_level, user_level, expected):
        assert AccessLevel.effective(org_level, user_level) is expected


class TestUserOrgAccess:
    @pytest.mark.parametrize(
        "role,expected",
        [(Role.ADMIN, AccessLevel.MANAGE), (Role.MEMBER, AccessLevel.STANDARD), (Role.VIEWER, AccessLevel.VIEW)],
        ids=["admin", "member", "viewer"],
    )
    def test_role_maps_to_level(self, role, expected, organization, user):
        assert user_org_access(make_membership(organization, user, role)) is expected

    def test_no_membership_has_no_level(self):
        assert user_org_access(None) is AccessLevel.NONE


class TestOrgProgramAccess:
    def test_program_org_manages(self, program):
        assert org_program_access(program.organization, program) is AccessLevel.MANAGE

    def test_funder_manages(self, program, funder_org):
        program.funder = funder_org
        program.save()
        assert org_program_access(funder_org, program) is AccessLevel.MANAGE

    def test_watcher_only_views(self, program, watcher_org):
        program.watchers.add(watcher_org)
        assert org_program_access(watcher_org, program) is AccessLevel.VIEW

    def test_unrelated_org_has_nothing(self, program, organization):
        assert org_program_access(organization, program) is AccessLevel.NONE

    @pytest.mark.parametrize("org,program_", [(None, True), (True, None)], ids=["no_org", "no_program"])
    def test_missing_side_has_nothing(self, org, program_, program, organization):
        assert org_program_access(organization if org else None, program if program_ else None) is AccessLevel.NONE
