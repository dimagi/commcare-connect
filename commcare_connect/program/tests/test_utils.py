import pytest

from commcare_connect.organization.models import UserOrganizationMembership
from commcare_connect.program.utils import AccessLevel, org_program_role, organization_role_level
from commcare_connect.utils.test_utils import make_membership

Role = UserOrganizationMembership.Role


class TestAccessLevelLattice:
    """The gates compare levels, so the ordering itself is load-bearing."""

    def test_levels_are_ordered(self):
        assert AccessLevel.NONE < AccessLevel.VIEW < AccessLevel.MANAGE

    @pytest.mark.parametrize(
        "relationship,organization_role,expected",
        [
            (AccessLevel.MANAGE, AccessLevel.MANAGE, AccessLevel.MANAGE),
            (AccessLevel.MANAGE, AccessLevel.VIEW, AccessLevel.VIEW),
            (AccessLevel.VIEW, AccessLevel.MANAGE, AccessLevel.VIEW),
            (AccessLevel.VIEW, AccessLevel.VIEW, AccessLevel.VIEW),
            (AccessLevel.MANAGE, AccessLevel.NONE, AccessLevel.NONE),
            (AccessLevel.NONE, AccessLevel.VIEW, AccessLevel.NONE),
        ],
    )
    def test_effective_takes_the_weaker_level(self, relationship, organization_role, expected):
        assert AccessLevel.effective(relationship, organization_role) is expected


class TestOrgProgramRole:
    def test_program_org_manages(self, program):
        assert org_program_role(program.organization, program) is AccessLevel.MANAGE

    def test_funder_manages(self, program, funder_org):
        program.funder = funder_org
        program.save()
        assert org_program_role(funder_org, program) is AccessLevel.MANAGE

    def test_watcher_views(self, program, watcher_org):
        program.watchers.add(watcher_org)
        assert org_program_role(watcher_org, program) is AccessLevel.VIEW

    def test_unrelated_org_has_no_role(self, program, organization):
        assert org_program_role(organization, program) is AccessLevel.NONE

    @pytest.mark.parametrize(
        "with_org,with_program",
        [(False, True), (True, False), (False, False)],
        ids=["no-org", "no-program", "neither"],
    )
    def test_missing_either_side_has_no_role(self, with_org, with_program, program, organization):
        org = organization if with_org else None
        prog = program if with_program else None
        assert org_program_role(org, prog) is AccessLevel.NONE


class TestOrganizationRoleLevel:
    @pytest.mark.parametrize(
        "role,expected",
        [(Role.ADMIN, AccessLevel.MANAGE), (Role.MEMBER, AccessLevel.VIEW), (Role.VIEWER, AccessLevel.VIEW)],
    )
    def test_role_maps_to_level(self, role, expected, organization, user):
        membership = make_membership(organization, user, role)
        assert organization_role_level(membership) is expected

    def test_no_membership_has_no_level(self):
        assert organization_role_level(None) is AccessLevel.NONE
