import pytest

from commcare_connect.opportunity.helpers import get_annotated_opportunity_access_deliver_status
from commcare_connect.opportunity.models import Opportunity
from commcare_connect.opportunity.tests.factories import (
    CompletedWorkFactory,
    OpportunityAccessFactory,
    PaymentUnitFactory,
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
