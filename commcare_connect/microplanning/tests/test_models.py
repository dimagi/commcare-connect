from __future__ import annotations

import pytest

from commcare_connect.microplanning.models import WorkAreaStatus
from commcare_connect.microplanning.tests.factories import (
    WorkAreaFactory,
    WorkAreaGroupFactory,
)
from commcare_connect.opportunity.tests.factories import OpportunityFactory


@pytest.mark.django_db
class TestWorkAreaGroupBuildingCount:
    @pytest.mark.parametrize(
        "work_areas, expected_count",
        [
            pytest.param(
                [(10, WorkAreaStatus.NOT_VISITED), (20, WorkAreaStatus.NOT_VISITED)],
                30,
                id="sums-non-excluded",
            ),
            pytest.param(
                [(10, WorkAreaStatus.NOT_VISITED), (50, WorkAreaStatus.EXCLUDED)],
                10,
                id="ignores-excluded",
            ),
            pytest.param(
                [(15, WorkAreaStatus.EXCLUDED)],
                0,
                id="zero-when-all-excluded",
            ),
        ],
    )
    def test_building_count(self, work_areas, expected_count):
        opp = OpportunityFactory()
        group = WorkAreaGroupFactory(opportunity=opp)
        for building_count, status in work_areas:
            WorkAreaFactory(
                opportunity=opp,
                work_area_group=group,
                building_count=building_count,
                status=status,
            )

        assert group.building_count == expected_count
