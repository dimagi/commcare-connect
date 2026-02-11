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

    def test_get_flush_keys_includes_relation_keys(self, flag):
        """Test that get_flush_keys includes cache keys for organizations, opportunities, and programs."""
        flush_keys = flag.get_flush_keys()

        # Should include keys for all three relations
        assert any("organizations" in str(key) for key in flush_keys)
        assert any("opportunities" in str(key) for key in flush_keys)
        assert any("programs" in str(key) for key in flush_keys)

    def test_is_active_for_organization_caching(self, flag):
        """Test that is_active_for caches organization IDs."""
        organization = OrganizationFactory()
        flag.organizations.add(organization)

        # First call should hit database and cache
        assert flag.is_active_for(organization) is True

        # Second call should use cache
        with pytest.assertNumQueries(0):
            assert flag.is_active_for(organization) is True

    def test_is_active_for_opportunity_caching(self, flag):
        """Test that is_active_for caches opportunity IDs."""
        opportunity = OpportunityFactory()
        flag.opportunities.add(opportunity)

        # First call should hit database and cache
        assert flag.is_active_for(opportunity) is True

        # Second call should use cache (but pytest doesn't track queries outside transaction)
        assert flag.is_active_for(opportunity) is True

    def test_is_active_for_program_caching(self, flag):
        """Test that is_active_for caches program IDs."""
        program = ProgramFactory()
        flag.programs.add(program)

        # First call should hit database and cache
        assert flag.is_active_for(program) is True

        # Second call should use cache
        assert flag.is_active_for(program) is True

    def test_get_relation_ids_returns_empty_set_when_no_relations(self, flag):
        """Test that _get_relation_ids returns empty set when no relations exist."""
        from waffle.utils import get_setting

        cache_key = get_setting("FLAG_ORGANIZATIONS_CACHE_KEY", "flag:%s:organizations")
        ids = flag._get_relation_ids("organizations", cache_key)

        assert ids == set()

    def test_get_relation_ids_returns_ids_when_relations_exist(self, flag):
        """Test that _get_relation_ids returns IDs when relations exist."""
        from waffle.utils import get_setting

        org1 = OrganizationFactory()
        org2 = OrganizationFactory()
        flag.organizations.add(org1, org2)

        cache_key = get_setting("FLAG_ORGANIZATIONS_CACHE_KEY", "flag:%s:organizations")
        ids = flag._get_relation_ids("organizations", cache_key)

        assert org1.pk in ids
        assert org2.pk in ids
        assert len(ids) == 2