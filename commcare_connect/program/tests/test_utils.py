import pytest
from django.test.client import RequestFactory

from commcare_connect.opportunity.tests.factories import OpportunityFactory
from commcare_connect.organization.models import UserOrganizationMembership
from commcare_connect.program.utils import (
    AccessLevel,
    is_opportunity_nm,
    is_opportunity_pm,
    opportunity_access_level_from_request,
    opportunity_by_id,
    org_access_level_from_request,
    org_opportunity_access,
    org_program_access,
    program_access_level_from_request,
    user_org_access,
)
from commcare_connect.users.tests.factories import OrganizationFactory
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

    def test_all_org_access_with_no_program_has_no_access(self, organization, user):
        """Nothing to have a relationship with, so not even ALL_ORG_ACCESS gets in."""
        request = make_request(grant_all_org_access(user), org=organization)
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


@pytest.fixture
def managed_opp(program, organization, supervisor_org):
    """A delivery org doing the work, a third-party supervisor, and the program's own owner."""
    return OpportunityFactory(
        program=program, organization=organization, supervising_organization=supervisor_org, managed=True
    )


@pytest.fixture
def opp_orgs(program, organization, supervisor_org, funder_org, watcher_org):
    """Every org with a distinct relationship to `managed_opp`."""
    program.funder = funder_org
    program.save()
    program.watchers.add(watcher_org)
    return {
        "delivery": organization,
        "supervisor": supervisor_org,
        "program_org": program.organization,
        "funder": funder_org,
        "watcher": watcher_org,
        "unrelated": OrganizationFactory(),
    }


class TestOrgOpportunityAccess:
    """The ceiling: what an org's relationship allows, before its users' roles cap it."""

    @pytest.mark.parametrize(
        "relationship,expected",
        [
            ("delivery", AccessLevel.MANAGE),
            ("supervisor", AccessLevel.MANAGE),
            ("program_org", AccessLevel.MANAGE),
            ("funder", AccessLevel.MANAGE),
            ("watcher", AccessLevel.VIEW),
            ("unrelated", AccessLevel.NONE),
        ],
    )
    def test_relationship_sets_the_ceiling(self, relationship, expected, opp_orgs, managed_opp):
        assert org_opportunity_access(opp_orgs[relationship], managed_opp) is expected

    @pytest.mark.parametrize("org,opp", [(None, True), (True, None)], ids=["no_org", "no_opportunity"])
    def test_missing_side_has_nothing(self, org, opp, managed_opp, organization):
        assert org_opportunity_access(organization if org else None, managed_opp if opp else None) is AccessLevel.NONE

    def test_the_program_side_reaches_even_a_non_managed_opportunity(self, program, organization):
        """Opportunity.program is non-null on every row, so a non-managed opp still has a program owner."""
        opp = OpportunityFactory(
            program=program, organization=organization, supervising_organization=organization, managed=False
        )
        assert org_opportunity_access(program.organization, opp) is AccessLevel.MANAGE


class TestOpportunityAccessLevelFromRequest:
    @pytest.mark.parametrize(
        "role,role_level",
        [(Role.ADMIN, AccessLevel.MANAGE), (Role.MEMBER, AccessLevel.STANDARD), (Role.VIEWER, AccessLevel.VIEW)],
        ids=["admin", "member", "viewer"],
    )
    @pytest.mark.parametrize("relationship", ["delivery", "supervisor"])
    def test_role_caps_the_ceiling(self, relationship, role, role_level, opp_orgs, managed_opp, user):
        org = opp_orgs[relationship]
        request = make_request(user, org=org, membership=make_membership(org, user, role))
        assert opportunity_access_level_from_request(request, managed_opp) is role_level

    def test_no_opportunity_has_no_access(self, organization, user):
        request = make_request(user, org=organization, membership=make_membership(organization, user, Role.ADMIN))
        assert opportunity_access_level_from_request(request, None) is AccessLevel.NONE

    def test_all_org_access_with_no_opportunity_has_no_access(self, organization, user):
        request = make_request(grant_all_org_access(user), org=organization)
        assert opportunity_access_level_from_request(request, None) is AccessLevel.NONE

    def test_all_org_access_overrides_an_unrelated_org(self, managed_opp, user):
        request = make_request(grant_all_org_access(user), org=OrganizationFactory())
        assert opportunity_access_level_from_request(request, managed_opp) is AccessLevel.MANAGE


class TestOpportunityParties:
    """
    One opportunity, several managing orgs: the delivery org is the NM, every other one acts as a PM.
    Watcher is neither
    """

    @pytest.mark.parametrize(
        "relationship,is_nm,is_pm",
        [
            ("delivery", True, False),
            ("supervisor", False, True),
            ("program_org", False, True),
            ("funder", False, True),
            ("watcher", False, False),
            ("unrelated", False, False),
        ],
    )
    def test_manage_access_splits_on_which_org_delivers(self, relationship, is_nm, is_pm, opp_orgs, managed_opp, user):
        org = opp_orgs[relationship]
        request = make_request(user, org=org, membership=make_membership(org, user, Role.ADMIN))
        assert is_opportunity_nm(request, managed_opp) is is_nm
        assert is_opportunity_pm(request, managed_opp) is is_pm

    @pytest.mark.parametrize("role", [Role.MEMBER, Role.VIEWER], ids=["member", "viewer"])
    def test_neither_party_without_manage_access(self, role, managed_opp, organization, user):
        """Being in the delivery org is not enough — the NM has to be able to manage it."""
        request = make_request(user, org=organization, membership=make_membership(organization, user, role))
        assert not is_opportunity_nm(request, managed_opp)
        assert not is_opportunity_pm(request, managed_opp)

    def test_all_org_access_takes_the_side_of_the_org_it_acts_as(self, managed_opp, organization, user):
        """The parties are read off the acting org, so the same user is either side depending on the slug."""
        user = grant_all_org_access(user)
        assert is_opportunity_nm(make_request(user, org=organization), managed_opp)
        assert is_opportunity_pm(make_request(user, org=OrganizationFactory()), managed_opp)


class TestOpportunityById:
    @pytest.mark.parametrize("id_field", ["opportunity_id", "pk"], ids=["uuid", "integer_pk"])
    def test_resolves_either_id_form(self, id_field, managed_opp):
        assert opportunity_by_id(str(getattr(managed_opp, id_field))) == managed_opp

    @pytest.mark.parametrize("opp_id", ["abc", "99999", "not-a-uuid"])
    def test_unresolvable_ids_return_none(self, opp_id, db):
        assert opportunity_by_id(opp_id) is None
