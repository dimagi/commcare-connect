import pytest

from commcare_connect.opportunity.tests.factories import OpportunityFactory
from commcare_connect.organization.decorators import user_is_opportunity_pm
from commcare_connect.organization.models import UserOrganizationMembership
from commcare_connect.users.tests.factories import UserFactory
from commcare_connect.utils.test_utils import make_membership

Role = UserOrganizationMembership.Role


class TestUserIsOpportunityPm:
    @pytest.fixture
    def opp(self, program, funder_org, supervisor_org, organization):
        program.funder = funder_org
        program.save()
        return OpportunityFactory(program=program, organization=organization, supervising_organization=supervisor_org)

    @pytest.mark.parametrize("relationship", ["program_org", "funder", "supervisor"])
    def test_admin_of_overseeing_org_is_pm(self, relationship, opp, program, funder_org, supervisor_org):
        org = {"program_org": program.organization, "funder": funder_org, "supervisor": supervisor_org}[relationship]
        user = UserFactory()
        make_membership(org, user, Role.ADMIN)
        assert user_is_opportunity_pm(user, opp) is True

    @pytest.mark.parametrize("role", [Role.MEMBER, Role.VIEWER])
    def test_non_admin_of_overseeing_org_is_not_pm(self, role, opp, program):
        user = UserFactory()
        make_membership(program.organization, user, role)
        assert user_is_opportunity_pm(user, opp) is False

    def test_executing_org_admin_is_not_pm(self, opp, organization):
        user = UserFactory()
        make_membership(organization, user, Role.ADMIN)
        assert user_is_opportunity_pm(user, opp) is False

    def test_unrelated_org_admin_is_not_pm(self, opp, watcher_org):
        user = UserFactory()
        make_membership(watcher_org, user, Role.ADMIN)
        assert user_is_opportunity_pm(user, opp) is False
