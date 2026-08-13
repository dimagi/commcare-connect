import pytest
from django.contrib.auth.models import Permission

from commcare_connect.opportunity.tests.factories import OpportunityFactory
from commcare_connect.organization.models import UserOrganizationMembership
from commcare_connect.program.tests.factories import ProgramFactory
from commcare_connect.program.utils import (
    AccessLevel,
    is_org_pm,
    org_program_role,
    organization_role_level,
    program_from_request,
    request_access_level,
    request_can_manage_program,
    request_can_view_program,
)
from commcare_connect.users.models import User
from commcare_connect.users.tests.factories import OrganizationFactory, UserFactory
from commcare_connect.utils.test_utils import StubRequest, make_membership

Role = UserOrganizationMembership.Role


def grant_all_org_access(user):
    user.user_permissions.add(Permission.objects.get(codename="all_org_access"))
    return User.objects.get(pk=user.pk)  # re-fetch to clear the cached perm set


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


class TestRequestAccessLevel:
    """The full matrix: relationship x internal role -> effective access level."""

    @pytest.fixture
    def related_orgs(self, program, funder_org, watcher_org, organization):
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
        "relationship,role,expected",
        [
            ("program_org", Role.ADMIN, AccessLevel.MANAGE),
            ("program_org", Role.MEMBER, AccessLevel.VIEW),
            ("program_org", Role.VIEWER, AccessLevel.VIEW),
            ("funder", Role.ADMIN, AccessLevel.MANAGE),
            ("funder", Role.MEMBER, AccessLevel.VIEW),
            ("funder", Role.VIEWER, AccessLevel.VIEW),
            ("watcher", Role.ADMIN, AccessLevel.VIEW),
            ("watcher", Role.MEMBER, AccessLevel.VIEW),
            ("watcher", Role.VIEWER, AccessLevel.VIEW),
            ("unrelated", Role.ADMIN, AccessLevel.NONE),
            ("unrelated", Role.MEMBER, AccessLevel.NONE),
            ("unrelated", Role.VIEWER, AccessLevel.NONE),
        ],
    )
    def test_matrix(self, relationship, role, expected, related_orgs, program):
        org = related_orgs[relationship]
        user = UserFactory()
        membership = make_membership(org, user, role)
        request = StubRequest(user=user, org=org, membership=membership, program=program)

        assert request_access_level(request) is expected
        assert request_can_manage_program(request) is (expected is AccessLevel.MANAGE)
        assert request_can_view_program(request) is (expected >= AccessLevel.VIEW)

    def test_no_membership_has_no_access(self, program):
        request = StubRequest(user=UserFactory(), org=program.organization, membership=None, program=program)
        assert request_access_level(request) is AccessLevel.NONE

    def test_all_org_access_overrides_every_relationship(self, program, organization):
        user = grant_all_org_access(UserFactory())
        request = StubRequest(user=user, org=organization, membership=None, program=program)
        assert request_access_level(request) is AccessLevel.MANAGE
        assert request_can_manage_program(request) is True
        assert request_can_view_program(request) is True

    def test_watcher_with_all_org_access_is_not_capped(self, program, watcher_org):
        program.watchers.add(watcher_org)
        user = grant_all_org_access(UserFactory())
        request = StubRequest(user=user, org=watcher_org, membership=None, program=program)
        assert request_can_manage_program(request) is True


class TestNoProgramInScopeFallback:
    """With no program resolvable (program:init, program:home) the legacy
    program-manager-org test applies, preserving is_org_pm exactly."""

    @pytest.mark.parametrize(
        "program_manager,role,expected",
        [
            (True, Role.ADMIN, True),
            (True, Role.MEMBER, False),
            (True, Role.VIEWER, False),
            (False, Role.ADMIN, False),
        ],
    )
    def test_falls_back_to_program_manager_flag(self, program_manager, role, expected, db):
        org = OrganizationFactory(program_manager=program_manager)
        user = UserFactory()
        membership = make_membership(org, user, role)
        request = StubRequest(user=user, org=org, membership=membership, resolver_kwargs={})

        assert request_can_manage_program(request) is expected
        assert is_org_pm(request) is expected

    @pytest.mark.parametrize("program_manager", [True, False])
    def test_superuser_manages_regardless_of_the_flag(self, program_manager, db):
        """is_org_pm dropped its explicit is_superuser branch; has_perm covers it,
        and no longer requires program_manager the way the old check did."""
        org = OrganizationFactory(program_manager=program_manager)
        request = StubRequest(user=UserFactory(is_superuser=True), org=org, membership=None, resolver_kwargs={})

        assert is_org_pm(request) is True


class TestProgramFromRequest:
    def test_resolves_from_request_opportunity(self, program):
        opp = OpportunityFactory(program=program)
        request = StubRequest(user=UserFactory(), opportunity=opp, resolver_kwargs={})
        assert program_from_request(request) == program

    def test_resolves_program_id_from_pk_kwarg(self, program):
        request = StubRequest(user=UserFactory(), resolver_kwargs={"pk": str(program.program_id)})
        assert program_from_request(request) == program

    def test_resolves_from_opp_id_kwarg(self, program):
        opp = OpportunityFactory(program=program)
        request = StubRequest(user=UserFactory(), resolver_kwargs={"opp_id": str(opp.opportunity_id)})
        assert program_from_request(request) == program

    def test_resolves_from_integer_opp_id_kwarg(self, program):
        opp = OpportunityFactory(program=program)
        request = StubRequest(user=UserFactory(), resolver_kwargs={"opp_id": str(opp.pk)})
        assert program_from_request(request) == program

    def test_non_uuid_pk_does_not_raise(self, db):
        """Other apps mount these gates on URLs whose `pk` is not a program_id."""
        request = StubRequest(user=UserFactory(), resolver_kwargs={"pk": "42"})
        assert program_from_request(request) is None

    def test_non_uuid_opp_id_does_not_raise(self, db):
        """<slug:opp_id> matches non-UUID strings, and filtering a UUIDField with one
        raises ValidationError -- a 500 instead of a 404."""
        request = StubRequest(user=UserFactory(), resolver_kwargs={"opp_id": "abc"})
        assert program_from_request(request) is None

    def test_pk_outside_the_program_namespace_is_ignored(self, program):
        """`pk` only means program_id on program URLs."""
        request = StubRequest(
            user=UserFactory(),
            resolver_kwargs={"pk": str(program.program_id)},
            app_names=("microplanning",),
        )
        assert program_from_request(request) is None

    def test_unknown_program_id_resolves_to_none(self, db):
        request = StubRequest(user=UserFactory(), resolver_kwargs={"pk": "0f9d0f4e-0000-4000-8000-000000000000"})
        assert program_from_request(request) is None

    def test_opportunity_wins_when_pk_and_opp_id_disagree(self, program):
        """program:opportunity_init_edit carries both kwargs, and the view edits the
        opportunity -- so a mismatched pk must not decide the gate."""
        other_program = ProgramFactory(organization=OrganizationFactory(program_manager=True))
        opp = OpportunityFactory(program=other_program)
        request = StubRequest(
            user=UserFactory(),
            resolver_kwargs={"pk": str(program.program_id), "opp_id": str(opp.opportunity_id)},
        )
        assert program_from_request(request) == other_program

    def test_unresolvable_opp_id_falls_through_to_pk(self, program):
        """A branch that cannot resolve must not suppress the next one."""
        request = StubRequest(
            user=UserFactory(),
            resolver_kwargs={"pk": str(program.program_id), "opp_id": "999999999"},
        )
        assert program_from_request(request) == program

    def test_result_is_memoized(self, program, django_assert_num_queries):
        request = StubRequest(user=UserFactory(), resolver_kwargs={"pk": str(program.program_id)})
        with django_assert_num_queries(1):
            program_from_request(request)
            program_from_request(request)
