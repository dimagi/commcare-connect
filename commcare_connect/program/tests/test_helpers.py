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

    @pytest.mark.parametrize(
        "scenario, visit_statuses, passing_assessments, expected_invited,"
        " expected_passing, expected_delivery, expected_conversion",
        [
            (
                "basic_scenario",
                [VisitValidationStatus.pending, VisitValidationStatus.pending, VisitValidationStatus.trial],
                [True, True, True],
                3,
                3,
                2,
                66.67,
            ),
            ("empty_scenario", [], [], 0, 0, 0, 0.0),
            ("multiple_visits_scenario", [VisitValidationStatus.pending], [True], 1, 1, 1, 100.0),
            (
                "excluded_statuses",
                [VisitValidationStatus.over_limit, VisitValidationStatus.trial],
                [True, True],
                2,
                2,
                0,
                0.0,
            ),
            (
                "failed_assessments",
                [VisitValidationStatus.pending, VisitValidationStatus.pending],
                [False, True],
                2,
                1,
                2,
                100.0,
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
        assert annotated_opp.workers_invited == expected_invited, f"Failed in {scenario}"
        assert annotated_opp.workers_passing_assessment == expected_passing, f"Failed in {scenario}"
        assert annotated_opp.workers_starting_delivery == expected_delivery, f"Failed in {scenario}"
        assert pytest.approx(annotated_opp.percentage_conversion, 0.01) == expected_conversion, f"Failed in {scenario}"
