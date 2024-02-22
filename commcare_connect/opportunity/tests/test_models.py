import pytest

from commcare_connect.opportunity.models import Opportunity
from commcare_connect.opportunity.tests.factories import (
    CompletedModuleFactory,
    LearnModuleFactory,
    OpportunityAccessFactory,
)


@pytest.mark.django_db
def test_learn_progress(opportunity: Opportunity):
    learn_modules = LearnModuleFactory.create_batch(2, app=opportunity.learn_app)
    access_1, access_2 = OpportunityAccessFactory.create_batch(2, opportunity=opportunity)
    for learn_module in learn_modules:
        CompletedModuleFactory(module=learn_module, opportunity_access=access_1)
    assert access_1.learn_progress == 100
    assert access_2.learn_progress == 0
