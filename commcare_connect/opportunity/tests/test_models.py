import pytest

from commcare_connect.opportunity.models import Opportunity, OpportunityAccess
from commcare_connect.opportunity.tests.factories import (
    CompletedModuleFactory,
    OpportunityAccessFactory,
    OpportunityClaimFactory,
    OpportunityClaimLimitFactory,
    PaymentUnitFactory,
)
from commcare_connect.users.models import User


@pytest.mark.django_db
def test_learn_progress():
    module = CompletedModuleFactory()
    access_1 = OpportunityAccessFactory(opportunity=module.opportunity, user=module.user)
    access_2 = OpportunityAccessFactory(opportunity=module.opportunity)
    assert access_1.learn_progress == 100
    assert access_2.learn_progress == 0


@pytest.mark.django_db
def test_opportunity_stats(opportunity: Opportunity, mobile_user: User):
    payment_unit_sub = PaymentUnitFactory(
        opportunity=opportunity, max_total=100, max_daily=10, amount=5, parent_payment_unit=None
    )
    payment_unit1 = PaymentUnitFactory(
        opportunity=opportunity,
        max_total=100,
        max_daily=10,
        amount=3,
        parent_payment_unit=payment_unit_sub,
    )
    payment_unit2 = PaymentUnitFactory(
        opportunity=opportunity, max_total=100, max_daily=10, amount=5, parent_payment_unit=None
    )
    assert set(list(opportunity.top_level_paymentunits.values_list("id", flat=True))) == {
        payment_unit1.id,
        payment_unit2.id,
    }

    # total 10 users (p1_amount * p1_max_total + p2_amount * p2_max_total)  * users
    opportunity.total_budget = 8000
    assert opportunity.budget_per_user == 800
    assert opportunity.number_of_users == 10

    access = OpportunityAccess.objects.get(opportunity=opportunity, user=mobile_user)
    claim = OpportunityClaimFactory(opportunity_access=access, max_payments=100)

    OpportunityClaimLimitFactory(opportunity_claim=claim, payment_unit=payment_unit1, max_visits=10)
    OpportunityClaimLimitFactory(opportunity_claim=claim, payment_unit=payment_unit2, max_visits=5)

    opportunity.claimed_budget == 80
