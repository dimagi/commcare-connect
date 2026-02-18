import pytest
from django.core.cache import cache
from django.test import RequestFactory

from commcare_connect.flags.models import Flag
from commcare_connect.flags.tests.factories import FlagFactory
from commcare_connect.opportunity.tests.factories import OpportunityAccessFactory, OpportunityFactory
from commcare_connect.program.tests.factories import ManagedOpportunityFactory, ProgramFactory
from commcare_connect.users.tests.factories import MembershipFactory, OrganizationFactory, UserFactory


@pytest.fixture
def flag():
    return FlagFactory()


@pytest.fixture
def program():
    return ProgramFactory()


@pytest.mark.django_db
class TestFlagModel:
    def setup_method(self):
        cache.clear()

    def test_flag_enabled(self, flag, organization, opportunity, program):
        flag.organizations.add(organization)
        assert flag.is_active_for(organization) is True

        flag.opportunities.add(opportunity)
        assert flag.is_active_for(opportunity) is True

        flag.programs.add(program)
        assert flag.is_active_for(program) is True

    def test_flag_not_enabled(self, flag, opportunity):
        flag.opportunities.add(opportunity)
        another_opp = OpportunityFactory()
        assert flag.is_active_for(another_opp) is False

    def test_invalid_object(self, flag):
        invalid_obj = {"name": "test"}
        assert flag.is_active_for(invalid_obj) is False

    def test_active_flags_for_user(self):
        user = UserFactory()
        user_flag = FlagFactory()
        FlagFactory()

        user_flag.users.add(user)
        active_flags = Flag.active_flags_for_user(user)
        assert active_flags.count() == 1
        assert active_flags[0] == user_flag

    def test_active_flags_for_user_segments(self):
        user = UserFactory()

        organization = OrganizationFactory()
        MembershipFactory(user=user, organization=organization)

        opportunity = OpportunityFactory()
        OpportunityAccessFactory(user=user, opportunity=opportunity)

        program = ProgramFactory(organization=organization)

        org_flag = FlagFactory()
        org_flag.organizations.add(organization)

        opportunity_flag = FlagFactory()
        opportunity_flag.opportunities.add(opportunity)

        program_flag = FlagFactory()
        program_flag.programs.add(program)

        FlagFactory()  # unrelated to user

        active_flags = Flag.active_flags_for_user(user)
        assert active_flags.count() == 3
        assert set(active_flags) == {org_flag, opportunity_flag, program_flag}

    def test_active_flags_for_user_role_flags(self):
        user = UserFactory(is_staff=True)
        staff_flag = FlagFactory(staff=True)
        everyone_flag = FlagFactory(everyone=True)
        FlagFactory(superusers=True)
        FlagFactory()  # unrelated to user

        active_flags = Flag.active_flags_for_user(user, include_role_flags=True)
        assert active_flags.count() == 2
        assert set(active_flags) == {staff_flag, everyone_flag}


@pytest.mark.django_db
class TestIsFlagActiveForRequest:
    def _make_request(self, user=None, org=None, opportunity=None):
        request = RequestFactory().get("/")
        if user is not None:
            request.user = user
        if org is not None:
            request.org = org
        if opportunity is not None:
            request.opportunity = opportunity
        return request

    def test_no_user_returns_false(self):
        flag = FlagFactory()
        request = RequestFactory().get("/")
        assert Flag.is_flag_active_for_request(request, flag.name) is False

    def test_unauthenticated_user_returns_false(self):
        flag = FlagFactory()
        request = RequestFactory().get("/")
        request.user = type("Anon", (), {"is_authenticated": False})()
        assert Flag.is_flag_active_for_request(request, flag.name) is False

    def test_flag_not_found_returns_false(self):
        user = UserFactory()
        request = self._make_request(user=user)
        assert Flag.is_flag_active_for_request(request, "nonexistent_flag") is False

    def test_flag_active_for_user(self):
        user = UserFactory()
        flag = FlagFactory()
        flag.users.add(user)
        request = self._make_request(user=user)
        assert Flag.is_flag_active_for_request(request, flag.name) is True

    def test_flag_not_active_for_user(self):
        user = UserFactory()
        flag = FlagFactory()
        request = self._make_request(user=user)
        assert Flag.is_flag_active_for_request(request, flag.name) is False

    def test_flag_active_for_organization(self):
        user = UserFactory()
        org = OrganizationFactory()
        flag = FlagFactory()
        flag.organizations.add(org)
        request = self._make_request(user=user, org=org)
        assert Flag.is_flag_active_for_request(request, flag.name) is True

    def test_flag_not_active_for_different_organization(self):
        user = UserFactory()
        org = OrganizationFactory()
        other_org = OrganizationFactory()
        flag = FlagFactory()
        flag.organizations.add(other_org)
        request = self._make_request(user=user, org=org)
        assert Flag.is_flag_active_for_request(request, flag.name) is False

    def test_flag_active_for_opportunity(self):
        user = UserFactory()
        opportunity = OpportunityFactory()
        flag = FlagFactory()
        flag.opportunities.add(opportunity)
        request = self._make_request(user=user, opportunity=opportunity)
        assert Flag.is_flag_active_for_request(request, flag.name) is True

    def test_flag_not_active_for_different_opportunity(self):
        user = UserFactory()
        opportunity = OpportunityFactory()
        other_opportunity = OpportunityFactory()
        flag = FlagFactory()
        flag.opportunities.add(other_opportunity)
        request = self._make_request(user=user, opportunity=opportunity)
        assert Flag.is_flag_active_for_request(request, flag.name) is False

    def test_flag_active_for_program_via_managed_opportunity(self):
        user = UserFactory()
        managed_opp = ManagedOpportunityFactory()
        program = managed_opp.managedopportunity.program
        flag = FlagFactory()
        flag.programs.add(program)
        request = self._make_request(user=user, opportunity=managed_opp)
        assert Flag.is_flag_active_for_request(request, flag.name) is True

    def test_flag_not_active_for_program_when_opportunity_not_managed(self):
        user = UserFactory()
        opportunity = OpportunityFactory()
        program = ProgramFactory()
        flag = FlagFactory()
        flag.programs.add(program)
        request = self._make_request(user=user, opportunity=opportunity)
        assert Flag.is_flag_active_for_request(request, flag.name) is False

    def test_flag_active_matches_by_name(self):
        user = UserFactory()
        flag = FlagFactory()
        other_flag = FlagFactory()
        other_flag.users.add(user)
        request = self._make_request(user=user)
        assert Flag.is_flag_active_for_request(request, flag.name) is False
        assert Flag.is_flag_active_for_request(request, other_flag.name) is True

    def test_org_flag_requires_org_on_request(self):
        user = UserFactory()
        org = OrganizationFactory()
        flag = FlagFactory()
        flag.organizations.add(org)
        request = self._make_request(user=user)
        assert Flag.is_flag_active_for_request(request, flag.name) is False

    def test_opportunity_flag_requires_opportunity_on_request(self):
        user = UserFactory()
        opportunity = OpportunityFactory()
        flag = FlagFactory()
        flag.opportunities.add(opportunity)
        request = self._make_request(user=user)
        assert Flag.is_flag_active_for_request(request, flag.name) is False

    def test_org_flag_does_not_activate_for_opportunity_only_request(self):
        user = UserFactory()
        org = OrganizationFactory()
        opportunity = OpportunityFactory(organization=org)
        flag = FlagFactory()
        flag.organizations.add(org)
        request = self._make_request(user=user, opportunity=opportunity)
        assert Flag.is_flag_active_for_request(request, flag.name) is False

    def test_opportunity_flag_does_not_activate_for_org_only_request(self):
        user = UserFactory()
        org = OrganizationFactory()
        opportunity = OpportunityFactory()
        flag = FlagFactory()
        flag.opportunities.add(opportunity)
        request = self._make_request(user=user, org=org)
        assert Flag.is_flag_active_for_request(request, flag.name) is False

    def test_program_flag_does_not_activate_for_org_only_request(self):
        user = UserFactory()
        org = OrganizationFactory()
        managed_opp = ManagedOpportunityFactory()
        program = managed_opp.managedopportunity.program
        flag = FlagFactory()
        flag.programs.add(program)
        request = self._make_request(user=user, org=org)
        assert Flag.is_flag_active_for_request(request, flag.name) is False

    def test_program_flag_does_not_activate_for_different_managed_opportunity(self):
        user = UserFactory()
        managed_opp_a = ManagedOpportunityFactory()
        managed_opp_b = ManagedOpportunityFactory()
        program_a = managed_opp_a.managedopportunity.program
        flag = FlagFactory()
        flag.programs.add(program_a)
        request = self._make_request(user=user, opportunity=managed_opp_b)
        assert Flag.is_flag_active_for_request(request, flag.name) is False

    def test_org_flag_activates_only_for_matching_org_among_multiple(self):
        user = UserFactory()
        org_a = OrganizationFactory()
        org_b = OrganizationFactory()
        flag = FlagFactory()
        flag.organizations.add(org_a)
        assert Flag.is_flag_active_for_request(self._make_request(user=user, org=org_a), flag.name) is True
        assert Flag.is_flag_active_for_request(self._make_request(user=user, org=org_b), flag.name) is False

    def test_opportunity_flag_activates_only_for_matching_opportunity_among_multiple(self):
        user = UserFactory()
        opp_a = OpportunityFactory()
        opp_b = OpportunityFactory()
        flag = FlagFactory()
        flag.opportunities.add(opp_a)
        assert Flag.is_flag_active_for_request(self._make_request(user=user, opportunity=opp_a), flag.name) is True
        assert Flag.is_flag_active_for_request(self._make_request(user=user, opportunity=opp_b), flag.name) is False

    def test_program_flag_activates_only_for_matching_program_among_multiple(self):
        user = UserFactory()
        managed_opp_a = ManagedOpportunityFactory()
        managed_opp_b = ManagedOpportunityFactory()
        program_a = managed_opp_a.managedopportunity.program
        flag = FlagFactory()
        flag.programs.add(program_a)
        assert (
            Flag.is_flag_active_for_request(self._make_request(user=user, opportunity=managed_opp_a), flag.name)
            is True
        )
        assert (
            Flag.is_flag_active_for_request(self._make_request(user=user, opportunity=managed_opp_b), flag.name)
            is False
        )

    def test_flag_active_via_org_when_request_has_both_org_and_opportunity(self):
        user = UserFactory()
        org = OrganizationFactory()
        opportunity = OpportunityFactory()
        flag = FlagFactory()
        flag.organizations.add(org)
        request = self._make_request(user=user, org=org, opportunity=opportunity)
        assert Flag.is_flag_active_for_request(request, flag.name) is True

    def test_flag_active_via_opportunity_when_request_has_both_org_and_opportunity(self):
        user = UserFactory()
        org = OrganizationFactory()
        opportunity = OpportunityFactory()
        flag = FlagFactory()
        flag.opportunities.add(opportunity)
        request = self._make_request(user=user, org=org, opportunity=opportunity)
        assert Flag.is_flag_active_for_request(request, flag.name) is True

    def test_no_entity_flag_activates_when_request_has_unrelated_org_and_opportunity(self):
        user = UserFactory()
        org = OrganizationFactory()
        opportunity = OpportunityFactory()
        other_org = OrganizationFactory()
        other_opp = OpportunityFactory()
        flag = FlagFactory()
        flag.organizations.add(other_org)
        flag.opportunities.add(other_opp)
        request = self._make_request(user=user, org=org, opportunity=opportunity)
        assert Flag.is_flag_active_for_request(request, flag.name) is False

    def test_program_flag_activates_when_request_has_explicit_org_and_managed_opportunity(self):
        user = UserFactory()
        org = OrganizationFactory()
        managed_opp = ManagedOpportunityFactory()
        program = managed_opp.managedopportunity.program
        flag = FlagFactory()
        flag.programs.add(program)
        request = self._make_request(user=user, org=org, opportunity=managed_opp)
        assert Flag.is_flag_active_for_request(request, flag.name) is True

    def test_program_flag_does_not_activate_for_wrong_program_when_request_has_explicit_org(self):
        user = UserFactory()
        org = OrganizationFactory()
        managed_opp_a = ManagedOpportunityFactory()
        managed_opp_b = ManagedOpportunityFactory()
        program_b = managed_opp_b.managedopportunity.program
        flag = FlagFactory()
        flag.programs.add(program_b)
        request = self._make_request(user=user, org=org, opportunity=managed_opp_a)
        assert Flag.is_flag_active_for_request(request, flag.name) is False
