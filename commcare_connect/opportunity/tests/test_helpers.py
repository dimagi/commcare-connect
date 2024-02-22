import pytest

from commcare_connect.opportunity.helpers import get_annotated_opportunity_access_deliver_status
from commcare_connect.opportunity.models import Opportunity
from commcare_connect.opportunity.tests.factories import OpportunityAccessFactory, UserVisitFactory
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
        assert access.visits_approved == 0
        assert access.visits_rejected == 0
        assert access.visits_pending == 0
        assert access.visits_over_limit == 0
        assert access.visits_completed == 0
        assert access.visits_duplicate == 0


@pytest.mark.django_db
def test_deliver_status_query(opportunity: Opportunity):
    mobile_users = MobileUserFactory.create_batch(5)
    user_visit_counts = {}
    for mobile_user in mobile_users:
        OpportunityAccessFactory(opportunity=opportunity, user=mobile_user, accepted=True)
        user_visits = UserVisitFactory.create_batch(20, opportunity=opportunity, user=mobile_user)
        count_by_status = dict(approved=0, pending=0, rejected=0, over_limit=0, completed=0, duplicate=0)
        for user_visit in user_visits:
            count_by_status[user_visit.status.value] += 1
        count_by_status["completed"] = len(user_visits)
        user_visit_counts[mobile_user.username] = count_by_status

    access_objects = get_annotated_opportunity_access_deliver_status(opportunity)
    for access in access_objects:
        username = access.user.username
        assert username in user_visit_counts
        assert user_visit_counts[username]["approved"] == access.visits_approved
        assert user_visit_counts[username]["rejected"] == access.visits_rejected
        assert user_visit_counts[username]["pending"] == access.visits_pending
        assert user_visit_counts[username]["over_limit"] == access.visits_over_limit
        assert user_visit_counts[username]["completed"] == access.visits_completed
        assert user_visit_counts[username]["duplicate"] == access.visits_duplicate


@pytest.mark.django_db
def test_deliver_status_query_visits_another_opportunity(opportunity: Opportunity):
    # Test user visit counts when visits are for another opportunity. Should return 0 for all counts as the user has
    # done no visits in the current opportunity.
    mobile_users = MobileUserFactory.create_batch(5)
    for mobile_user in mobile_users:
        access = OpportunityAccessFactory(opportunity=opportunity, user=mobile_user, accepted=True)
        UserVisitFactory.create_batch(5, user=mobile_user, opportunity_access=access)
    access_objects = get_annotated_opportunity_access_deliver_status(opportunity)
    usernames = {user.username for user in mobile_users}
    for access in access_objects:
        assert access.uservisit_set.count() == 5
        assert access.user.username in usernames
        assert access.visits_approved == 0
        assert access.visits_rejected == 0
        assert access.visits_pending == 0
        assert access.visits_over_limit == 0
        assert access.visits_completed == 0
        assert access.visits_duplicate == 0
