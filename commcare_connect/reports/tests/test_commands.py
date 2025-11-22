import datetime
from datetime import timezone

from django.core.management import call_command

from commcare_connect.opportunity.models import CompletedWork, CompletedWorkStatus
from commcare_connect.opportunity.tests.factories import (
    AssessmentFactory,
    CompletedWorkFactory,
    OpportunityAccessFactory,
    OpportunityClaimFactory,
    OpportunityFactory,
    PaymentFactory,
    PaymentUnitFactory,
)
from commcare_connect.reports.models import UserAnalyticsData
from commcare_connect.users.tests.factories import UserFactory


class TestBackfillUserAnalyticsData:
    def test_data_for_connect_user(self, db, httpx_mock):
        httpx_mock.add_response(method="GET", json={"data": [{"username": "test"}]})
        user = UserFactory(username="test", email=None)
        for idx, opportunity in enumerate(OpportunityFactory.create_batch(2)):
            payment_unit = PaymentUnitFactory(opportunity=opportunity, max_total=1)

            access = OpportunityAccessFactory(
                user=user,
                opportunity=opportunity,
                date_learn_started=datetime.datetime(2023 + idx, 1, 2, tzinfo=timezone.utc),
                completed_learn_date=datetime.datetime(2023 + idx, 1, 3, tzinfo=timezone.utc),
                accepted=True,
            )
            access.invited_date = datetime.datetime(2023 + idx, 1, 1, tzinfo=timezone.utc)
            access.save()

            assessment = AssessmentFactory(
                opportunity_access=access,
                passed=True,
                date=datetime.datetime(2023 + idx, 1, 4, tzinfo=timezone.utc),
            )

            claim = OpportunityClaimFactory(opportunity_access=access)
            claim.date_claimed = datetime.datetime(2023 + idx, 1, 5, tzinfo=timezone.utc)
            claim.save()

            completed_work = CompletedWorkFactory(
                opportunity_access=access,
                payment_unit=payment_unit,
                status=CompletedWorkStatus.approved,
                status_modified_date=datetime.datetime(2023 + idx, 1, 9, tzinfo=timezone.utc),
            )
            completed_work.date_created = datetime.datetime(2023 + idx, 1, 6, tzinfo=timezone.utc)
            completed_work.save()

            payment = PaymentFactory(
                opportunity_access=access,
                date_paid=datetime.datetime(2023 + idx, 1, 7, tzinfo=timezone.utc),
            )

        assert not UserAnalyticsData.objects.filter(user=user).exists()
        call_command("backfill_user_analytics_data")
        assert UserAnalyticsData.objects.filter(user=user).exists()

        analytics_data = UserAnalyticsData.objects.get(user=user)
        assert analytics_data.username == user.username
        assert analytics_data.has_opp_invite == access.invited_date
        assert analytics_data.has_started_learning == access.date_learn_started
        assert analytics_data.has_completed_learning == access.completed_learn_date
        assert analytics_data.has_completed_assessment == assessment.date
        assert analytics_data.has_claimed_job == claim.date_claimed
        first_cw = CompletedWork.objects.filter(opportunity_access__user=user).order_by("date_created").first()
        assert analytics_data.has_ever_earned_payment == first_cw.status_modified_date
        assert analytics_data.has_started_job == first_cw.date_created
        assert analytics_data.has_paid == payment.date_paid
        assert analytics_data.has_completed_opp == completed_work.status_modified_date
        assert analytics_data.has_offered_multiple_opps == access.invited_date
        assert analytics_data.has_accepted_multiple_opps == access.invited_date
        assert analytics_data.has_completed_multiple_opps == completed_work.status_modified_date

    def test_data_for_personalid_user(self, db, httpx_mock):
        httpx_mock.add_response(method="GET", json={"data": [{"username": "test"}]})

        assert not UserAnalyticsData.objects.filter(username="test", user__isnull=True).exists()
        call_command("backfill_user_analytics_data")
        assert UserAnalyticsData.objects.filter(username="test", user__isnull=True).exists()

        analytics_data = UserAnalyticsData.objects.get(username="test")
        assert analytics_data.username == "test"
        assert analytics_data.has_opp_invite is None
        assert analytics_data.has_ever_earned_payment is None
        assert analytics_data.has_started_learning is None
        assert analytics_data.has_completed_learning is None
        assert analytics_data.has_completed_assessment is None
        assert analytics_data.has_claimed_job is None
        assert analytics_data.has_started_job is None
        assert analytics_data.has_paid is None
        assert analytics_data.has_completed_opp is None
        assert analytics_data.has_offered_multiple_opps is None
        assert analytics_data.has_accepted_multiple_opps is None
        assert analytics_data.has_completed_multiple_opps is None
