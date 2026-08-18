import pytest
from django.test.client import RequestFactory

from commcare_connect.organization.models import UserOrganizationMembership
from commcare_connect.program.utils import (
    AccessLevel,
    org_access_level_from_request,
    org_program_access,
    program_access_level_from_request,
    user_org_access,
)
from commcare_connect.utils.test_utils import grant_all_org_access, make_membership

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


def make_request(user, org=None, membership=None):
    """A request carrying only what the access functions read: the user, the org, the role in it."""
    request = RequestFactory().get("/")
    request.user = user
    request.org = org
    request.org_membership = membership
    return request


class TestProgramAccessLevelFromRequest:
    """relationship x internal role -> effective level."""

    @pytest.fixture
    def orgs(self, program, funder_org, watcher_org, organization):
        program.funder = funder_org
        program.save()
        program.watchers.add(watcher_org)
        return {
            "program_org": program.organization,
            "funder": funder_org,
            "watcher": watcher_org,
            "unrelated": organization,
        }

    @pytest.mark.parametrize(
        "relationship,org_access",
        [
            ("program_org", AccessLevel.MANAGE),
            ("funder", AccessLevel.MANAGE),
            ("watcher", AccessLevel.VIEW),
            ("unrelated", AccessLevel.NONE),
        ],
    )
    @pytest.mark.parametrize(
        "role,role_level",
        [(Role.ADMIN, AccessLevel.MANAGE), (Role.MEMBER, AccessLevel.STANDARD), (Role.VIEWER, AccessLevel.VIEW)],
        ids=["admin", "member", "viewer"],
    )
    def test_matrix(self, relationship, org_access, role, role_level, orgs, program, user):
        org = orgs[relationship]
        membership = make_membership(org, user, role)
        request = make_request(user, org=org, membership=membership)
        assert program_access_level_from_request(request, program) is min(org_access, role_level)

    def test_no_membership_has_no_access(self, program, user):
        request = make_request(user, org=program.organization)
        assert program_access_level_from_request(request, program) is AccessLevel.NONE

    def test_no_org_has_no_access_even_with_all_org_access(self, program, user):
        request = make_request(grant_all_org_access(user))
        assert program_access_level_from_request(request, program) is AccessLevel.NONE

    def test_all_org_access_overrides_an_unrelated_org(self, program, organization, user):
        request = make_request(grant_all_org_access(user), org=organization)
        assert program_access_level_from_request(request, program) is AccessLevel.MANAGE

    def test_no_program_has_no_access(self, program_manager_org, user):
        """Nothing to have a relationship with, so not even a PM org's admin gets in."""
        membership = make_membership(program_manager_org, user, Role.ADMIN)
        request = make_request(user, org=program_manager_org, membership=membership)
        assert program_access_level_from_request(request, None) is AccessLevel.NONE


class TestOrgAccessLevelFromRequest:
    @pytest.mark.parametrize(
        "role,expected",
        [(Role.ADMIN, AccessLevel.MANAGE), (Role.MEMBER, AccessLevel.STANDARD), (Role.VIEWER, AccessLevel.VIEW)],
        ids=["admin", "member", "viewer"],
    )
    def test_role_is_the_level(self, role, expected, organization, user):
        membership = make_membership(organization, user, role)
        assert org_access_level_from_request(make_request(user, org=organization, membership=membership)) is expected

    def test_no_membership_has_no_access(self, organization, user):
        assert org_access_level_from_request(make_request(user, org=organization)) is AccessLevel.NONE

    def test_all_org_access_manages(self, organization, user):
        request = make_request(grant_all_org_access(user), org=organization)
        assert org_access_level_from_request(request) is AccessLevel.MANAGE

    def test_no_org_has_no_access_even_with_all_org_access(self, user):
        """A slug that matches no org leaves nothing to act as, which ALL_ORG_ACCESS cannot supply."""
        assert org_access_level_from_request(make_request(grant_all_org_access(user))) is AccessLevel.NONE

    def test_the_program_manager_flag_is_irrelevant(self, program_manager_org, organization, user):
        """Unlike the program resolver, nothing here consults the org's relationship to a program."""
        pm_membership = make_membership(program_manager_org, user, Role.MEMBER)
        plain_membership = make_membership(organization, user, Role.MEMBER)
        pm_request = make_request(user, org=program_manager_org, membership=pm_membership)
        plain_request = make_request(user, org=organization, membership=plain_membership)
        assert org_access_level_from_request(pm_request) is org_access_level_from_request(plain_request)

