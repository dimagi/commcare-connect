import pytest
from django.db import IntegrityError, transaction

from commcare_connect.opportunity.models import Opportunity
from commcare_connect.opportunity.tests.factories import OpportunityFactory, PaymentUnitFactory
from commcare_connect.program.tests.factories import ProgramApplicationFactory


@pytest.mark.django_db
def test_managed_opportunity_stats():
    opportunity = OpportunityFactory(total_budget=3600000)
    PaymentUnitFactory(opportunity=opportunity, max_total=600, max_daily=5, amount=750, org_amount=450)

    opportunity = Opportunity.objects.get(id=opportunity.id)

    assert opportunity.budget_per_user == 450000
    assert opportunity.allotted_visits == 3000
    assert opportunity.number_of_users == 5
    assert opportunity.max_visits_per_user == 600
    assert opportunity.daily_max_visits_per_user == 5
    assert opportunity.budget_per_visit == 750


@pytest.mark.django_db
def test_an_organization_cannot_hold_two_applications_to_one_program():
    application = ProgramApplicationFactory()

    with pytest.raises(IntegrityError), transaction.atomic():
        ProgramApplicationFactory(program=application.program, organization=application.organization)


@pytest.mark.django_db
def test_an_organization_may_apply_to_two_different_programs():
    application = ProgramApplicationFactory()

    other = ProgramApplicationFactory(organization=application.organization)

    assert other.organization == application.organization
    assert other.program != application.program
