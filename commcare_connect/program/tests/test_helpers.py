from datetime import timedelta

import pytest
from django_celery_beat.utils import now

from commcare_connect.opportunity.models import VisitValidationStatus
from commcare_connect.opportunity.tests.factories import AssessmentFactory, OpportunityAccessFactory, UserVisitFactory
from commcare_connect.program.helpers import get_annotated_managed_opportunity
from commcare_connect.program.tests.factories import ManagedOpportunityFactory, ProgramFactory
from commcare_connect.users.tests.factories import OrganizationFactory, UserFactory


class TestGetAnnotatedManagedOpportunity:
    @pytest.fixture(autouse=True)
    def setup(self, db):
        self.program = ProgramFactory.create()
        self.nm_org = OrganizationFactory.create()
        self.opp = ManagedOpportunityFactory.create(program=self.program, organization=self.nm_org)

    def create_user_with_access(self, visit_status=VisitValidationStatus.pending, passed_assessment=True):
        user = UserFactory.create()
        access = OpportunityAccessFactory.create(opportunity=self.opp, user=user, invited_date=now())
        AssessmentFactory.create(opportunity=self.opp, user=user, opportunity_access=access, passed=passed_assessment)
        UserVisitFactory.create(
            user=user,
            opportunity=self.opp,
            status=visit_status,
            opportunity_access=access,
            visit_date=now() + timedelta(days=1),
        )
        return user

    def test_basic_scenario(self):
        for i in range(5):
            self.create_user_with_access(
                visit_status=VisitValidationStatus.pending if i < 3 else VisitValidationStatus.trial
            )

        opps = get_annotated_managed_opportunity(self.program)
        assert len(opps) == 1
        annotated_opp = opps[0]
        assert annotated_opp.organization.slug == self.nm_org.slug
        assert annotated_opp.workers_invited == 5
        assert annotated_opp.workers_passing_assessment == 5
        assert annotated_opp.workers_starting_delivery == 3
        assert annotated_opp.percentage_conversion == 60.0

    def test_empty_scenario(self):
        opps = get_annotated_managed_opportunity(self.program)
        assert len(opps) == 1
        annotated_opp = opps[0]
        assert annotated_opp.workers_invited == 0
        assert annotated_opp.workers_passing_assessment == 0
        assert annotated_opp.workers_starting_delivery == 0
        assert annotated_opp.percentage_conversion == 0.0
        assert annotated_opp.average_time_to_convert is None

    def test_multiple_visits(self):
        user = self.create_user_with_access()
        UserVisitFactory.create_batch(
            2,
            user=user,
            opportunity=self.opp,
            status=VisitValidationStatus.pending,
            opportunity_access=user.opportunityaccess_set.first(),
            visit_date=now() + timedelta(days=2),
        )

        opps = get_annotated_managed_opportunity(self.program)
        assert len(opps) == 1
        annotated_opp = opps[0]
        assert annotated_opp.workers_invited == 1
        assert annotated_opp.workers_passing_assessment == 1
        assert annotated_opp.workers_starting_delivery == 1
        assert annotated_opp.percentage_conversion == 100.0

    def test_excluded_statuses(self):
        self.create_user_with_access(visit_status=VisitValidationStatus.over_limit)
        self.create_user_with_access(visit_status=VisitValidationStatus.trial)

        opps = get_annotated_managed_opportunity(self.program)
        assert len(opps) == 1
        annotated_opp = opps[0]
        assert annotated_opp.workers_invited == 2
        assert annotated_opp.workers_passing_assessment == 2
        assert annotated_opp.workers_starting_delivery == 0
        assert annotated_opp.percentage_conversion == 0.0

    def test_average_time_to_convert(self):
        for i in range(3):
            user = self.create_user_with_access()
            user.opportunityaccess_set.update(invited_date=now() - timedelta(days=i))

        opps = get_annotated_managed_opportunity(self.program)
        assert len(opps) == 1
        annotated_opp = opps[0]
        expected_time = timedelta(days=2)
        actual_time = annotated_opp.average_time_to_convert
        assert abs(actual_time - expected_time) < timedelta(seconds=5)

    def test_multiple_opportunities(self):
        nm_org2 = OrganizationFactory.create()
        opp2 = ManagedOpportunityFactory.create(
            program=self.program, organization=nm_org2, start_date=now() + timedelta(days=1)
        )

        self.create_user_with_access()
        user2 = UserFactory.create()
        access2 = OpportunityAccessFactory.create(opportunity=opp2, user=user2, invited_date=now())
        AssessmentFactory.create(opportunity=opp2, user=user2, opportunity_access=access2, passed=True)
        UserVisitFactory.create(
            user=user2,
            opportunity=opp2,
            status=VisitValidationStatus.pending,
            opportunity_access=access2,
            visit_date=now() + timedelta(days=1),
        )

        opps = get_annotated_managed_opportunity(self.program)
        assert len(opps) == 2
        assert opps[0].organization.slug == self.nm_org.slug
        assert opps[1].organization.slug == nm_org2.slug
        for annotated_opp in opps:
            assert annotated_opp.workers_invited == 1
            assert annotated_opp.workers_passing_assessment == 1
            assert annotated_opp.workers_starting_delivery == 1
            assert annotated_opp.percentage_conversion == 100.0

    def test_failed_assessments(self):
        self.create_user_with_access(passed_assessment=False)
        self.create_user_with_access(passed_assessment=True)

        opps = get_annotated_managed_opportunity(self.program)
        assert len(opps) == 1
        annotated_opp = opps[0]
        assert annotated_opp.workers_invited == 2
        assert annotated_opp.workers_passing_assessment == 1
        assert annotated_opp.workers_starting_delivery == 2
        assert annotated_opp.percentage_conversion == 100.0
