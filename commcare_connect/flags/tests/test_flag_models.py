import pytest
from django.core.cache import cache

from commcare_connect.flags.models import Flag
from commcare_connect.flags.tests.factories import FlagFactory
from commcare_connect.opportunity.tests.factories import OpportunityAccessFactory, OpportunityFactory
from commcare_connect.program.tests.factories import ProgramFactory
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
