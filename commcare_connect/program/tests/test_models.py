import pytest

from commcare_connect.opportunity.tests.factories import PaymentUnitFactory
from commcare_connect.program.models import ManagedOpportunity
from commcare_connect.program.tests.factories import ManagedOpportunityFactory


@pytest.mark.django_db
def test_managed_opportunity_stats():
    opportunity = ManagedOpportunityFactory(total_budget=3600000)
    PaymentUnitFactory(opportunity=opportunity, max_total=600, max_daily=5, amount=750, org_amount=450)

    opportunity = ManagedOpportunity.objects.get(id=opportunity.id)

    assert opportunity.budget_per_user == 450000
    assert opportunity.allotted_visits == 3000
    assert opportunity.number_of_users == 5
    assert opportunity.max_visits_per_user == 600
    assert opportunity.daily_max_visits_per_user == 5
    assert opportunity.budget_per_visit == 750
