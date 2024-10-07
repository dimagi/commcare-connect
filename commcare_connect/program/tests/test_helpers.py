from datetime import timedelta

import pytest
from django_celery_beat.utils import now

from commcare_connect.opportunity.models import VisitValidationStatus
from commcare_connect.opportunity.tests.factories import (
    AssessmentFactory,
    CompletedWorkFactory,
    OpportunityAccessFactory,
    UserVisitFactory,
)
from commcare_connect.program.helpers import get_annotated_managed_opportunity, get_delivery_performance_report
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


@pytest.mark.django_db
class TestDeliveryPerformanceReport:
    @pytest.fixture(autouse=True)
    def setup(self):
        self.program_manager_org = OrganizationFactory.create()
        self.program = ProgramFactory.create(organization=self.program_manager_org)
        self.nm_org = OrganizationFactory.create()
        self.opp = ManagedOpportunityFactory.create(program=self.program, organization=self.nm_org)

    def create_user_with_visit(self, visit_status, visit_date, flagged=False, create_completed_work=True):
        user = UserFactory.create()
        access = OpportunityAccessFactory.create(opportunity=self.opp, user=user, invited_date=now())
        UserVisitFactory.create(
            user=user,
            opportunity=self.opp,
            status=visit_status,
            opportunity_access=access,
            visit_date=visit_date,
            flagged=flagged,
        )
        if create_completed_work:
            CompletedWorkFactory.create(opportunity_access=access)
        return user

    def test_basic_delivery_performance(self):
        for _ in range(2):
            self.create_user_with_visit(VisitValidationStatus.pending, now(), True)
        for _ in range(3):
            self.create_user_with_visit(VisitValidationStatus.approved, now(), False)

        opps = get_delivery_performance_report(self.program, None, None)
        assert len(opps) == 1
        assert opps[0].total_workers_starting_delivery == 5
        assert opps[0].active_workers == 5
        assert opps[0].total_payment_units == 5
        assert opps[0].total_payment_units_with_flags == 2
        assert opps[0].total_payment_since_start_date == 5
        assert opps[0].delivery_per_day_per_worker == 1.0
        assert opps[0].records_flagged_percentage == 40.0

    def test_delivery_performance_with_date_range(self):
        start_date = now() - timedelta(10)
        end_date = now() + timedelta(10)

        self.create_user_with_visit(VisitValidationStatus.pending, start_date - timedelta(1))
        self.create_user_with_visit(VisitValidationStatus.pending, start_date + timedelta(1))
        self.create_user_with_visit(VisitValidationStatus.pending, end_date - timedelta(1))
        self.create_user_with_visit(VisitValidationStatus.pending, end_date + timedelta(1))

        opps = get_delivery_performance_report(self.program, start_date, end_date)
        assert opps[0].active_workers == 2
        assert opps[0].total_payment_since_start_date == 2

    def test_delivery_performance_with_flagged_visits(self):
        self.create_user_with_visit(VisitValidationStatus.pending, now())
        self.create_user_with_visit(VisitValidationStatus.pending, now(), flagged=True)

        opps = get_delivery_performance_report(self.program, None, None)
        assert opps[0].total_payment_units_with_flags == 1
        assert opps[0].records_flagged_percentage == 50.0

    def test_delivery_performance_with_no_active_workers(self):
        self.create_user_with_visit(VisitValidationStatus.over_limit, now())
        self.create_user_with_visit(VisitValidationStatus.trial, now())

        opps = get_delivery_performance_report(self.program, None, None)
        assert opps[0].total_workers_starting_delivery == 0
        assert opps[0].active_workers == 0
        assert opps[0].delivery_per_day_per_worker == 0.0

    def test_delivery_performance_with_multiple_opportunities(self):
        opp2 = ManagedOpportunityFactory.create(program=self.program)

        self.create_user_with_visit(VisitValidationStatus.pending, now())

        user = UserFactory.create()
        access = OpportunityAccessFactory.create(opportunity=opp2, user=user, invited_date=now())
        UserVisitFactory.create(
            user=user,
            opportunity=opp2,
            status=VisitValidationStatus.pending,
            opportunity_access=access,
            visit_date=now(),
        )
        CompletedWorkFactory.create(opportunity_access=access)

        opps = get_delivery_performance_report(self.program, None, None)
        assert len(opps) == 2
        assert all(o.active_workers == 1 for o in opps)

    def test_delivery_performance_with_no_completed_work(self):
        self.create_user_with_visit(VisitValidationStatus.pending, now(), create_completed_work=False)

        opps = get_delivery_performance_report(self.program, None, None)
        assert opps[0].total_payment_units == 0
        assert opps[0].delivery_per_day_per_worker == 0

    @pytest.mark.parametrize("visit_status", [VisitValidationStatus.rejected, VisitValidationStatus.approved])
    def test_delivery_performance_excluded_statuses(self, visit_status):
        self.create_user_with_visit(visit_status, now(), flagged=True)

        opps = get_delivery_performance_report(self.program, None, None)
        assert opps[0].total_workers_starting_delivery == 1
        assert opps[0].active_workers == 1
        assert opps[0].total_payment_units_with_flags == 0

    def test_delivery_performance_with_mixed_statuses(self):
        self.create_user_with_visit(VisitValidationStatus.pending, now(), flagged=True)
        self.create_user_with_visit(VisitValidationStatus.approved, now(), flagged=True)
        self.create_user_with_visit(VisitValidationStatus.rejected, now(), flagged=True)
        self.create_user_with_visit(VisitValidationStatus.over_limit, now(), flagged=True)

        opps = get_delivery_performance_report(self.program, None, None)
        assert opps[0].total_workers_starting_delivery == 3
        assert opps[0].active_workers == 3
        assert opps[0].total_payment_units_with_flags == 2
        assert opps[0].records_flagged_percentage == 66.67
