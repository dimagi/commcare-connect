import pytest
from django.core.exceptions import ValidationError

from commcare_connect.microplanning.models import WorkAreaGroup
from commcare_connect.microplanning.services.work_area_groups import clear_work_area_groups_for_opportunity
from commcare_connect.microplanning.tests.factories import WorkAreaFactory, WorkAreaGroupFactory
from commcare_connect.opportunity.tests.factories import OpportunityAccessFactory


@pytest.mark.django_db
class TestClearWorkAreaGroupsForOpportunity:
    def test_deletes_only_this_opportunitys_groups(self, opportunity):
        WorkAreaGroupFactory(opportunity=opportunity)
        other_group = WorkAreaGroupFactory()

        clear_work_area_groups_for_opportunity(opportunity)

        assert list(WorkAreaGroup.objects.all()) == [other_group]

    def test_refuses_while_work_areas_are_assigned(self, opportunity):
        WorkAreaGroupFactory(opportunity=opportunity)
        WorkAreaFactory(opportunity=opportunity, opportunity_access=OpportunityAccessFactory(opportunity=opportunity))

        with pytest.raises(ValidationError):
            clear_work_area_groups_for_opportunity(opportunity)

        assert WorkAreaGroup.objects.filter(opportunity=opportunity).exists()
