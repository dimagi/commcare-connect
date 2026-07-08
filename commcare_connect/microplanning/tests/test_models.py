from __future__ import annotations

import pytest
from django.contrib.gis.geos import Point, Polygon
from django.db import IntegrityError

from commcare_connect.microplanning.models import SRID, ImplementationArea, WorkAreaStatus
from commcare_connect.microplanning.tests.factories import (
    ImplementationAreaFactory,
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


@pytest.mark.django_db
def test_implementation_area_creation(opportunity):
    ia = ImplementationArea.objects.create(
        opportunity=opportunity,
        name="Ward 1",
        centroid=Point(77.1, 28.6, srid=SRID),
        boundary=Polygon(((77, 28), (78, 28), (78, 29), (77, 29), (77, 28)), srid=SRID),
    )
    assert ia.pk is not None
    assert str(ia) == f"Ward 1-{opportunity.id}"


@pytest.mark.django_db
def test_implementation_area_name_unique_per_opportunity(opportunity):
    ImplementationAreaFactory(opportunity=opportunity, name="Ward 1")
    with pytest.raises(IntegrityError):
        ImplementationAreaFactory(opportunity=opportunity, name="Ward 1")


@pytest.mark.django_db
def test_work_area_links_to_implementation_area(opportunity):
    ia = ImplementationAreaFactory(opportunity=opportunity)
    wa = WorkAreaFactory(opportunity=opportunity, implementation_area=ia)
    assert wa.implementation_area == ia
    ia.delete()
    wa.refresh_from_db()
    assert wa.implementation_area is None
