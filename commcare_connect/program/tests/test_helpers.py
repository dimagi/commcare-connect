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


@pytest.mark.django_db
class BaseManagedOpportunityTest:
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

    def create_user_with_visit(self, visit_status, visit_date, flagged=False, create_completed_work=True):
        user = UserFactory.create()
        access = OpportunityAccessFactory.create(opportunity=self.opp, user=user, invited_date=now())
        visit = UserVisitFactory.create(
            user=user,
            opportunity=self.opp,
            status=visit_status,
            opportunity_access=access,
            visit_date=visit_date,
            flagged=flagged,
        )
        if create_completed_work:
            work = CompletedWorkFactory.create(opportunity_access=access)
            visit.completed_work = work
            visit.save()
        return user


class TestGetAnnotatedManagedOpportunity(BaseManagedOpportunityTest):
    @pytest.mark.parametrize(
        "scenario, visit_statuses, passing_assessments, expected_invited,"
        " expected_passing, expected_delivery, expected_conversion, expected_avg_time_to_convert",
        [
            (
                "basic_scenario",
                [VisitValidationStatus.pending, VisitValidationStatus.pending, VisitValidationStatus.trial],
                [True, True, True],
                3,
                3,
                2,
                66.67,
                timedelta(days=1),
            ),
            ("empty_scenario", [], [], 0, 0, 0, 0.0, timedelta(days=0)),
            ("multiple_visits_scenario", [VisitValidationStatus.pending], [True], 1, 1, 1, 100.0, timedelta(days=1)),
            (
                "excluded_statuses",
                [VisitValidationStatus.over_limit, VisitValidationStatus.trial],
                [True, True],
                2,
                2,
                0,
                0.0,
                timedelta(days=0),
            ),
            (
                "failed_assessments",
                [VisitValidationStatus.pending, VisitValidationStatus.pending],
                [False, True],
                2,
                1,
                2,
                100.0,
                timedelta(days=1),
            ),
        ],
    )
    def test_scenarios(
        self,
        scenario,
        visit_statuses,
        passing_assessments,
        expected_invited,
        expected_passing,
        expected_delivery,
        expected_conversion,
        expected_avg_time_to_convert,
    ):
        for i, visit_status in enumerate(visit_statuses):
            user = self.create_user_with_access(visit_status=visit_status, passed_assessment=passing_assessments[i])

            # For the "multiple_visits_scenario", create additional visits for the same user
            if scenario == "multiple_visits_scenario":
                access = user.opportunityaccess_set.first()
                UserVisitFactory.create_batch(
                    2,
                    user=user,
                    opportunity=self.opp,
                    status=VisitValidationStatus.pending,
                    opportunity_access=access,
                    visit_date=now() + timedelta(days=2),
                )
        opps = get_annotated_managed_opportunity(self.program)
        assert len(opps) == 1
        annotated_opp = opps[0]
        assert annotated_opp.workers_invited == expected_invited
        assert annotated_opp.workers_passing_assessment == expected_passing
        assert annotated_opp.workers_starting_delivery == expected_delivery
        assert pytest.approx(annotated_opp.percentage_conversion, 0.01) == expected_conversion

        diff = abs(annotated_opp.average_time_to_convert - expected_avg_time_to_convert)
        assert diff < timedelta(minutes=1)


@pytest.mark.django_db
class TestDeliveryPerformanceReport(BaseManagedOpportunityTest):
    start_date = now() - timedelta(10)
    end_date = now() + timedelta(10)

    @pytest.mark.parametrize(
        "scenario, visit_statuses, visit_date, flagged_statuses, expected_active_workers, "
        "expected_total_workers, expected_records_flagged_percentage,"
        "total_payment_units_with_flags,total_payment_since_start_date, deliveries_per_worker",
        [
            (
                "basic_scenario",
                [VisitValidationStatus.pending] * 2 + [VisitValidationStatus.approved] * 3,
                [now()] * 5,
                [True] * 3 + [False] * 2,
                5,
                5,
                60.0,
                3,
                5,
                1.0,
            ),
            (
                "date_range_scenario",
                [VisitValidationStatus.pending] * 4,
                [
                    now() - timedelta(8),
                    now() + timedelta(11),
                    now() - timedelta(9),
                    now() + timedelta(11),
                ],
                [False] * 4,
                2,
                4,
                0.0,
                0,
                2,
                1.0,
            ),
            (
                "flagged_visits_scenario",
                [VisitValidationStatus.pending, VisitValidationStatus.pending],
                [now()] * 2,
                [False, True],
                2,
                2,
                50.0,
                1,
                2,
                1.0,
            ),
            (
                "no_active_workers_scenario",
                [VisitValidationStatus.over_limit, VisitValidationStatus.trial],
                [now(), now()],
                [False, False],
                0,
                0,
                0.0,
                0,
                0,
                0.0,
            ),
            (
                "mixed_statuses_scenario",
                [
                    VisitValidationStatus.pending,
                    VisitValidationStatus.approved,
                    VisitValidationStatus.rejected,
                    VisitValidationStatus.over_limit,
                ],
                [now()] * 4,
                [True] * 4,
                3,
                3,
                100,
                3,
                3,
                1.0,
            ),
        ],
    )
    def test_delivery_performance_report_scenarios(
        self,
        scenario,
        visit_statuses,
        visit_date,
        flagged_statuses,
        expected_active_workers,
        expected_total_workers,
        expected_records_flagged_percentage,
        total_payment_units_with_flags,
        total_payment_since_start_date,
        deliveries_per_worker,
    ):
        for i, visit_status in enumerate(visit_statuses):
            self.create_user_with_visit(
                visit_status=visit_status, visit_date=visit_date[i], flagged=flagged_statuses[i]
            )

        start_date = end_date = None
        if scenario == "date_range_scenario":
            start_date = now() - timedelta(10)
            end_date = now() + timedelta(10)

        opps = get_delivery_performance_report(self.program, start_date, end_date)

        assert len(opps) == 1
        assert opps[0].active_workers == expected_active_workers
        assert opps[0].total_workers_starting_delivery == expected_total_workers
        assert opps[0].records_flagged_percentage == expected_records_flagged_percentage
        assert opps[0].total_payment_units_with_flags == total_payment_units_with_flags
        assert opps[0].total_payment_since_start_date == total_payment_since_start_date
        assert opps[0].deliveries_per_worker == deliveries_per_worker


@pytest.mark.django_db
def test_average_time_to_convert_for_negative_values():
    program = ProgramFactory.create()
    nm_org = OrganizationFactory.create()
    opp = ManagedOpportunityFactory.create(program=program, organization=nm_org)
    today = now()

    valid_durations = []

    for r in range(4):
        user = UserFactory.create()
        access = OpportunityAccessFactory.create(opportunity=opp, user=user, invited_date=now())
        AssessmentFactory.create(opportunity=opp, user=user, opportunity_access=access, passed=True)

        if r % 2 == 0:
            visit_date = today - timedelta(days=1)
        else:
            visit_date = today + timedelta(days=4)
            duration = visit_date - access.invited_date  # Positive duration
            valid_durations.append(duration)  # Only count valid (positive) durations

        UserVisitFactory.create(
            user=user,
            opportunity=opp,
            opportunity_access=access,
            visit_date=visit_date,
            status=VisitValidationStatus.approved,
        )

    opp = get_annotated_managed_opportunity(program)[0]

    expected_avg = sum(valid_durations, timedelta()) / len(valid_durations)
    assert opp.average_time_to_convert == expected_avg
