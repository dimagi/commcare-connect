import uuid
from datetime import timedelta

import pytest
from django.utils.timezone import now

from commcare_connect.opportunity.helpers import (
    get_annotated_opportunity_access_deliver_status,
    get_worker_learn_table_data,
    get_worker_table_data,
)
from commcare_connect.opportunity.models import Opportunity
from commcare_connect.opportunity.tests.factories import (
    AssessmentFactory,
    CompletedModuleFactory,
    CompletedWorkFactory,
    LearnModuleFactory,
    OpportunityAccessFactory,
    PaymentUnitFactory,
    UserVisitFactory,
)
from commcare_connect.users.tests.factories import MobileUserFactory


@pytest.mark.django_db
def test_deliver_status_query_no_visits(opportunity: Opportunity):
    mobile_users = MobileUserFactory.create_batch(5)
    for mobile_user in mobile_users:
        OpportunityAccessFactory(opportunity=opportunity, user=mobile_user, accepted=True)
    access_objects = get_annotated_opportunity_access_deliver_status(opportunity)

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

    access_objects = get_annotated_opportunity_access_deliver_status(opportunity)
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
    access_objects = get_annotated_opportunity_access_deliver_status(opportunity)
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

    access = OpportunityAccessFactory(opportunity=opportunity)

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
    module3 = LearnModuleFactory(app=opportunity.learn_app)

    access = OpportunityAccessFactory(opportunity=opportunity)

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
    for r in result:
        print(r.id)
    row = result.get(id=access.id)

    assert row.last_active.date() == three_days_ago
    assert row.completed_learn == None
    assert row.passed_assessment is True
    assert row.assesment_count == 2
    assert row.learning_hours.total_seconds() == 10800
    assert row.completed_modules_count == 2
    assert row.modules_completed_percentage == round(2 * 100.0 / 3, 1)
