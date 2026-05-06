from __future__ import annotations

import pytest

from commcare_connect.microplanning.models import WorkAreaStatus
from commcare_connect.microplanning.tests.factories import WorkAreaFactory, WorkAreaGroupFactory
from commcare_connect.opportunity.tests.factories import OpportunityFactory


@pytest.mark.django_db
class TestWorkAreaGroupBuildingCount:
    def test_sums_building_counts_of_non_excluded_work_areas(self):
        opp = OpportunityFactory()
        group = WorkAreaGroupFactory(opportunity=opp)
        WorkAreaFactory(opportunity=opp, work_area_group=group, building_count=10)
        WorkAreaFactory(opportunity=opp, work_area_group=group, building_count=20)

        assert group.building_count == 30

    def test_excludes_excluded_work_areas_from_building_count(self):
        opp = OpportunityFactory()
        group = WorkAreaGroupFactory(opportunity=opp)
        WorkAreaFactory(opportunity=opp, work_area_group=group, building_count=10)
        WorkAreaFactory(
            opportunity=opp,
            work_area_group=group,
            building_count=50,
            status=WorkAreaStatus.EXCLUDED,
        )

        assert group.building_count == 10

    def test_building_count_is_zero_when_all_work_areas_excluded(self):
        opp = OpportunityFactory()
        group = WorkAreaGroupFactory(opportunity=opp)
        WorkAreaFactory(
            opportunity=opp,
            work_area_group=group,
            building_count=15,
            status=WorkAreaStatus.EXCLUDED,
        )

        assert group.building_count == 0
