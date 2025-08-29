import uuid
from datetime import timedelta

import pytest
from django.utils.timezone import now

from commcare_connect.opportunity.helpers import (
    get_annotated_opportunity_access_deliver_status,
    get_opportunity_delivery_progress,
    get_opportunity_funnel_progress,
    get_opportunity_worker_progress,
    get_worker_learn_table_data,
    get_worker_table_data,
)
from commcare_connect.opportunity.models import (
    CompletedWorkStatus,
    Opportunity,
    UserInviteStatus,
    VisitValidationStatus,
)
from commcare_connect.opportunity.tests.factories import (
    AssessmentFactory,
    CompletedModuleFactory,
    CompletedWorkFactory,
    LearnModuleFactory,
    OpportunityAccessFactory,
    OpportunityClaimFactory,
    PaymentFactory,
    PaymentUnitFactory,
    UserInviteFactory,
    UserVisitFactory,
)
from commcare_connect.users.tests.factories import MobileUserFactory


@pytest.mark.django_db
def test_deliver_status_query_no_visits(opportunity: Opportunity):
    mobile_users = MobileUserFactory.create_batch(5)
    for mobile_user in mobile_users:
        OpportunityAccessFactory(opportunity=opportunity, user=mobile_user, accepted=True)
    access_objects = get_annotated_opportunity_access_deliver_status(opportunity, {})

    usernames = {user.username for user in mobile_users}
    for access in access_objects:
        assert access.user.username in usernames
        assert access.approved == 0
        assert access.rejected == 0
        assert access.pending == 0
        assert access.completed == 0


@pytest.mark.django_db
def test_deliver_status_query(opportunity: Opportunity):
    mobile_users = MobileUserFactory.create_batch(5)
    completed_work_counts = {}
    payment_units = PaymentUnitFactory.create_batch(2, opportunity=opportunity)
    for mobile_user in mobile_users:
        access = OpportunityAccessFactory(opportunity=opportunity, user=mobile_user, accepted=True)
        for pu in payment_units:
            count_by_status = dict(approved=0, pending=0, rejected=0, completed=0, over_limit=0, incomplete=0)
            completed_works = CompletedWorkFactory.create_batch(20, opportunity_access=access, payment_unit=pu)
            for cw in completed_works:
                count_by_status[cw.status.value] += 1
            count_by_status["completed"] = len(completed_works) - count_by_status["incomplete"]
            completed_work_counts[(mobile_user.username, pu.name)] = count_by_status

    access_objects = get_annotated_opportunity_access_deliver_status(opportunity, {})
    for access in access_objects:
        username = access.user.username
        assert (username, access.payment_unit) in completed_work_counts
        assert completed_work_counts[(username, access.payment_unit)]["approved"] == access.approved
        assert completed_work_counts[(username, access.payment_unit)]["rejected"] == access.rejected
        assert completed_work_counts[(username, access.payment_unit)]["pending"] == access.pending
        assert completed_work_counts[(username, access.payment_unit)]["completed"] == access.completed
        assert completed_work_counts[(username, access.payment_unit)]["over_limit"] == access.over_limit
        assert completed_work_counts[(username, access.payment_unit)]["incomplete"] == access.incomplete


@pytest.mark.django_db
def test_deliver_status_query_visits_another_opportunity(opportunity: Opportunity):
    # Test user visit counts when visits are for another opportunity. Should return 0 for all counts as the user has
    # done no visits in the current opportunity.
    mobile_users = MobileUserFactory.create_batch(5)
    for mobile_user in mobile_users:
        OpportunityAccessFactory(opportunity=opportunity, user=mobile_user, accepted=True)
        CompletedWorkFactory.create_batch(5)
    access_objects = get_annotated_opportunity_access_deliver_status(opportunity, {})
    usernames = {user.username for user in mobile_users}
    for access in access_objects:
        assert access.user.username in usernames
        assert access.approved == 0
        assert access.rejected == 0
        assert access.pending == 0
        assert access.completed == 0


@pytest.mark.django_db
def test_get_worker_table_data_all_fields(opportunity):
    today = now().date()
    five_days_ago = today - timedelta(days=5)
    three_days_ago = today - timedelta(days=3)
    two_days_ago = today - timedelta(days=2)

    opportunity.end_date = today + timedelta(days=5)
    opportunity.save()
    opportunity.refresh_from_db()

    module1 = LearnModuleFactory(app=opportunity.learn_app)
    module2 = LearnModuleFactory(app=opportunity.learn_app)

    access = OpportunityAccessFactory(opportunity=opportunity, last_active=today)

    # Completed modules
    CompletedModuleFactory(
        opportunity=opportunity,
        opportunity_access=access,
        user=access.user,
        module=module1,
        date=five_days_ago,
    )
    CompletedModuleFactory(
        xform_id=uuid.uuid4(),
        opportunity=opportunity,
        opportunity_access=access,
        user=access.user,
        module=module1,
        date=today,
    )
    CompletedModuleFactory(
        opportunity=opportunity,
        opportunity_access=access,
        user=access.user,
        module=module2,
        date=three_days_ago,
    )

    UserVisitFactory(
        opportunity=opportunity,
        opportunity_access=access,
        user=access.user,
        visit_date=two_days_ago,
    )

    access.date_learn_started = five_days_ago
    access.save()
    result = get_worker_table_data(opportunity)
    row = result.get(id=access.id)

    assert row.completed_learn.date() == three_days_ago
    assert row.days_to_complete_learn.days == 2
    assert row.first_delivery.date() == two_days_ago
    assert row.days_to_start_delivery.days == (row.first_delivery.date() - access.date_learn_started).days
    assert row.last_active.date() == today


@pytest.mark.django_db
def test_get_worker_learn_table_data_all_fields(
    opportunity,
):
    today = now().date()
    five_days_ago = today - timedelta(days=5)
    three_days_ago = today - timedelta(days=3)

    opportunity.end_date = today + timedelta(days=5)
    opportunity.save()

    module1 = LearnModuleFactory(app=opportunity.learn_app)
    module2 = LearnModuleFactory(app=opportunity.learn_app)
    LearnModuleFactory(app=opportunity.learn_app)

    access = OpportunityAccessFactory(opportunity=opportunity, accepted=True, last_active=three_days_ago)

    # Completed 2 out of 3 modules
    CompletedModuleFactory(
        opportunity=opportunity,
        opportunity_access=access,
        user=access.user,
        module=module1,
        date=five_days_ago,
        duration=timedelta(hours=1),
    )
    CompletedModuleFactory(
        xform_id=uuid.uuid4(),
        opportunity=opportunity,
        opportunity_access=access,
        user=access.user,
        module=module2,
        date=three_days_ago,
        duration=timedelta(hours=2),
    )
    # Passed assessment
    AssessmentFactory(
        user=access.user,
        opportunity=opportunity,
        opportunity_access=access,
        passed=True,
        score=85,
        passing_score=70,
        date=today,
    )

    # Failed assessment (shouldn't affect passed_assessment=True)
    AssessmentFactory(
        user=access.user,
        opportunity=opportunity,
        opportunity_access=access,
        passed=False,
        score=50,
        passing_score=70,
        date=three_days_ago,
    )

    result = get_worker_learn_table_data(opportunity)
    row = result.get(id=access.id)

    assert row.last_active.date() == three_days_ago
    assert row.completed_learn_date is None
    assert row.assesment_count == 2
    assert row.learning_hours.total_seconds() == 10800
    assert row.completed_modules_count == 2
    assert row.modules_completed_percentage == round(2 * 100.0 / 3, 1)


@pytest.mark.django_db
@pytest.mark.parametrize(
    "opportunity",
    [
        {"opp_options": {"managed": True}},
    ],
    indirect=True,
)
def test_opportunity_delivery_stats(opportunity):
    today = now()
    yesterday = today - timedelta(days=1)
    day_before_yesterday = yesterday - timedelta(days=1)

    users = MobileUserFactory.create_batch(4)

    oa1 = OpportunityAccessFactory(
        opportunity=opportunity, user=users[0], last_active=yesterday, accepted=True, payment_accrued=100
    )
    oa2 = OpportunityAccessFactory(
        opportunity=opportunity, user=users[1], last_active=yesterday, accepted=True, payment_accrued=200
    )
    oa3 = OpportunityAccessFactory(
        opportunity=opportunity, user=users[2], accepted=True, payment_accrued=300, last_active=yesterday
    )
    OpportunityAccessFactory.create_batch(
        3, opportunity=opportunity, last_active=yesterday - timedelta(days=3)
    )  # not active uses 3

    # invited count 3 pending count = 1 not found should not be counted
    UserInviteFactory(opportunity=opportunity, opportunity_access=oa1, status=UserInviteStatus.accepted)
    UserInviteFactory(opportunity=opportunity, opportunity_access=oa2, status=UserInviteStatus.accepted)
    UserInviteFactory(opportunity=opportunity, opportunity_access=oa3, status=UserInviteStatus.not_found)
    UserInviteFactory(opportunity=opportunity, status=UserInviteStatus.invited)

    total_accrued = 600
    total_paid = 150
    payment_due = total_accrued - total_paid

    # total deliveries=4 deliveries_from_yesterday=3
    cw = CompletedWorkFactory(opportunity_access=oa1, status_modified_date=now(), status=CompletedWorkStatus.pending)
    UserVisitFactory.create(
        opportunity=opportunity,
        opportunity_access=oa1,
        status=VisitValidationStatus.pending,
        completed_work=cw,
        visit_date=yesterday,
    )

    # accrued_since_yesterday=10
    cw = CompletedWorkFactory(
        opportunity_access=oa2,
        status_modified_date=now(),
        status=CompletedWorkStatus.approved,
        saved_payment_accrued=10,
    )
    UserVisitFactory.create(
        opportunity=opportunity,
        opportunity_access=oa2,
        status=VisitValidationStatus.approved,
        completed_work=cw,
        visit_date=today,
    )

    UserVisitFactory.create(
        opportunity=opportunity, opportunity_access=oa1, status=VisitValidationStatus.approved, completed_work=cw
    )
    UserVisitFactory.create(
        opportunity=opportunity,
        opportunity_access=oa2,
        status=VisitValidationStatus.pending,
        completed_work=cw,
        visit_date=today,
    )

    cw = CompletedWorkFactory(opportunity_access=oa1, status_modified_date=now(), status=CompletedWorkStatus.pending)
    UserVisitFactory.create(
        opportunity=opportunity,
        opportunity_access=oa2,
        status=VisitValidationStatus.approved,
        completed_work=cw,
        visit_date=day_before_yesterday,
        review_created_on=now() - timedelta(days=2),
    )

    cw = CompletedWorkFactory(opportunity_access=oa2, status_modified_date=now(), status=CompletedWorkStatus.pending)
    UserVisitFactory.create(
        opportunity=opportunity,
        opportunity_access=oa2,
        status=VisitValidationStatus.approved,
        completed_work=cw,
        visit_date=day_before_yesterday,
        review_created_on=now(),
    )

    # recent date paid will be today total paid should be 150
    PaymentFactory(opportunity_access=oa1, date_paid=yesterday, amount=100)
    PaymentFactory(opportunity_access=oa2, date_paid=today, amount=50)

    result = get_opportunity_delivery_progress(opportunity.id)

    assert opportunity.id == result.id
    assert result.total_paid == total_paid
    assert result.total_accrued == total_accrued
    assert result.payments_due == payment_due
    assert result.inactive_workers == 3
    assert result.deliveries_from_yesterday == 3
    assert result.accrued_since_yesterday == 10
    assert result.most_recent_delivery == today
    assert result.total_deliveries == 4
    assert result.flagged_deliveries_waiting_for_review == 2
    assert result.flagged_deliveries_waiting_for_review_since_yesterday == 2
    assert result.visits_pending_for_pm_review == 2
    assert result.visits_pending_for_pm_review_since_yesterday == 1
    assert result.recent_payment == today
    assert result.workers_invited == 3
    assert result.pending_invites == 1


@pytest.mark.django_db
@pytest.mark.parametrize(
    "opportunity",
    [
        {"opp_options": {"managed": True}},
    ],
    indirect=True,
)
def test_opportunity_worker_progress_stats(opportunity):
    today = now()
    yesterday = today - timedelta(days=1)

    access = OpportunityAccessFactory(opportunity=opportunity, accepted=True, payment_accrued=300)

    # Total paid = 100
    PaymentFactory(opportunity_access=access, date_paid=today, amount=100)

    # Total deliveries = 4
    CompletedWorkFactory.create_batch(2, opportunity_access=access, status=CompletedWorkStatus.pending)
    CompletedWorkFactory.create_batch(1, opportunity_access=access, status=CompletedWorkStatus.approved)
    CompletedWorkFactory.create_batch(1, opportunity_access=access, status=CompletedWorkStatus.rejected)

    # Visits since yesterday = 2
    cw = CompletedWorkFactory(opportunity_access=access, status=CompletedWorkStatus.pending)
    UserVisitFactory(
        opportunity=opportunity,
        opportunity_access=access,
        completed_work=cw,
        status=VisitValidationStatus.pending,
        visit_date=yesterday,
    )
    UserVisitFactory(
        opportunity=opportunity,
        opportunity_access=access,
        completed_work=cw,
        status=VisitValidationStatus.approved,
        visit_date=yesterday,
    )

    result = get_opportunity_worker_progress(opportunity.id)

    assert result.id == opportunity.id
    assert result.total_deliveries == 5
    assert result.approved_deliveries == 1
    assert result.rejected_deliveries == 1
    assert result.total_accrued == 300
    assert result.total_paid == 100
    assert result.visits_since_yesterday == 2


@pytest.mark.django_db
@pytest.mark.parametrize(
    "opportunity",
    [
        {"opp_options": {"managed": True}},
    ],
    indirect=True,
)
def test_get_opportunity_funnel_progress(opportunity):
    today = now()
    user1 = OpportunityAccessFactory(
        opportunity=opportunity, accepted=True, date_learn_started=today, completed_learn_date=today
    )
    user2 = OpportunityAccessFactory(opportunity=opportunity, accepted=True, date_learn_started=today)
    user3 = OpportunityAccessFactory(opportunity=opportunity, accepted=True)

    UserInviteFactory(opportunity=opportunity, opportunity_access=user1, status=UserInviteStatus.accepted)
    UserInviteFactory(opportunity=opportunity, opportunity_access=user2, status=UserInviteStatus.invited)
    UserInviteFactory(opportunity=opportunity, opportunity_access=user3, status=UserInviteStatus.accepted)

    # Claimed job
    OpportunityClaimFactory(opportunity_access=user1)
    OpportunityClaimFactory(opportunity_access=user2)

    # Deliveries started (UserVisit exists)
    UserVisitFactory(opportunity=opportunity, opportunity_access=user1)

    # Completed assessments
    AssessmentFactory(opportunity=opportunity, user=user1.user, passed=True)
    AssessmentFactory(opportunity=opportunity, user=user2.user, passed=True)
    AssessmentFactory(opportunity=opportunity, user=user3.user, passed=False)  # shouldn't count

    result = get_opportunity_funnel_progress(opportunity.id)

    assert result.id == opportunity.id
    assert result.workers_invited == 3
    assert result.pending_invites == 1
    assert result.started_learning_count == 2
    assert result.completed_learning == 1
    assert result.claimed_job == 2
    assert result.started_deliveries == 1
    assert result.completed_assessments == 2


@pytest.mark.django_db
@pytest.mark.parametrize(
    "filters,expected_usernames",
    [
        # last_active
        ({"last_active": 5}, lambda users: {users["mobile_user1"]}),
        # has_duplicates
        ({"has_duplicates": True}, lambda users: {users["mobile_user1"]}),
        (
            {"has_duplicates": False},
            lambda users: {users["mobile_user2"], users["mobile_user3"], users["mobile_user4"]},
        ),
        # has_overlimit
        ({"has_overlimit": True}, lambda users: {users["mobile_user2"]}),
        (
            {"has_overlimit": False},
            lambda users: {users["mobile_user1"], users["mobile_user3"], users["mobile_user4"]},
        ),
        # review_pending
        ({"review_pending": True}, lambda users: {users["mobile_user1"]}),
        (
            {"review_pending": False},
            lambda users: {users["mobile_user2"], users["mobile_user3"], users["mobile_user4"]},
        ),
        # has_flags
        ({"has_flags": True}, lambda users: {users["mobile_user3"]}),
        ({"has_flags": False}, lambda users: {users["mobile_user1"], users["mobile_user2"], users["mobile_user4"]}),
        # filters combination
        ({"has_duplicates": True, "review_pending": True}, lambda users: {users["mobile_user1"]}),
        ({"has_duplicates": True, "review_pending": False}, lambda users: set()),
    ],
)
def test_deliver_status_query_with_filters(opportunity, filters, expected_usernames):
    payment_unit = PaymentUnitFactory(opportunity=opportunity)

    mobile_user1 = MobileUserFactory()
    access1 = OpportunityAccessFactory(
        opportunity=opportunity, user=mobile_user1, accepted=True, last_active=now() - timedelta(days=10)
    )
    CompletedWorkFactory(
        opportunity_access=access1, payment_unit=payment_unit, status=CompletedWorkStatus.pending, entity_id="a"
    )
    CompletedWorkFactory(
        opportunity_access=access1,
        payment_unit=payment_unit,
        status=CompletedWorkStatus.pending,
        entity_id="b",
        saved_completed_count=2,
    )

    mobile_user2 = MobileUserFactory()
    access2 = OpportunityAccessFactory(opportunity=opportunity, user=mobile_user2, accepted=True, last_active=now())
    CompletedWorkFactory(opportunity_access=access2, payment_unit=payment_unit, status=CompletedWorkStatus.over_limit)

    mobile_user3 = MobileUserFactory()
    access3 = OpportunityAccessFactory(opportunity=opportunity, user=mobile_user3, accepted=True, last_active=now())
    UserVisitFactory(opportunity_access=access3, deliver_unit__payment_unit=payment_unit, flagged=True)

    mobile_user4 = MobileUserFactory()
    OpportunityAccessFactory(opportunity=opportunity, user=mobile_user4, accepted=True, last_active=now())

    users = {
        "mobile_user1": mobile_user1.username,
        "mobile_user2": mobile_user2.username,
        "mobile_user3": mobile_user3.username,
        "mobile_user4": mobile_user4.username,
    }

    access_objects = get_annotated_opportunity_access_deliver_status(opportunity, filters)
    usernames = {a.user.username for a in access_objects}

    assert usernames == expected_usernames(users)
