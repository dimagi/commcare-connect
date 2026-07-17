from __future__ import annotations

import pytest
from django.contrib.gis.geos import Point, Polygon

from commcare_connect.microplanning.models import SRID, WorkAreaStatus
from commcare_connect.microplanning.tests.factories import WorkAreaFactory, WorkAreaGroupFactory


@pytest.mark.django_db
class TestWorkAreaGroupModel:
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
        group = WorkAreaGroupFactory()
        for building_count, status in work_areas:
            WorkAreaFactory(
                opportunity=group.opportunity,
                work_area_group=group,
                building_count=building_count,
                status=status,
            )

        assert group.building_count == expected_count

    def test_update_centroid(self, django_assert_num_queries):
        """Really simple test plots 4 Work Areas that share a center at 78, 29."""
        group = WorkAreaGroupFactory()
        work_areas = []
        for x, y in [(77, 28), (77, 29), (78, 28), (78, 29)]:
            work_area = WorkAreaFactory(
                opportunity=group.opportunity,
                work_area_group=group,
                centroid=Point(x, y, srid=SRID),
                boundary=Polygon(
                    (
                        (x, y),
                        (x, y + 1),
                        (x + 1, y + 1),
                        (x + 1, y),
                        (x, y),
                    ),
                    srid=SRID,
                ),
            )
            work_areas.append(work_area)

        # happy-path
        assert group.centroid is None
        with django_assert_num_queries(2):
            group.update_centroid()
            assert group.centroid.x == 78
            assert group.centroid.y == 29

        # re-run with no change to centroid value
        with django_assert_num_queries(1):
            group.update_centroid()
            assert group.centroid.x == 78
            assert group.centroid.y == 29

        # exclude first 2 work areas, moves the x value to 78.5
        work_areas[0].work_area_group = None
        work_areas[0].save()
        work_areas[1].work_area_group = None
        work_areas[1].save()

        with django_assert_num_queries(2):
            group.update_centroid()
            assert group.centroid is not None
            assert group.centroid.x == 78.5
            assert group.centroid.y == 29
