import pytest
from django.contrib.gis.geos import Polygon

from commcare_connect.microplanning.models import SRID, WorkAreaStatus
from commcare_connect.microplanning.overlap import find_overlapping_work_areas
from commcare_connect.microplanning.tests.factories import WorkAreaFactory, square


def find_pairs(new_work_areas):
    rows = find_overlapping_work_areas([wa.pk for wa in new_work_areas])
    return {(row["pk"], row["other_id"]): round(row["ratio"], 3) for row in rows}


@pytest.mark.django_db
class TestFindOverlappingWorkAreas:
    @pytest.mark.parametrize(
        "offset, overlaps",
        [
            (0.96, False),  # 4% of either square
            (0.94, True),  # 6%
            (1.0, False),  # shared edge only
            (2.0, False),  # apart
        ],
    )
    def test_threshold(self, opportunity, offset, overlaps):
        existing = WorkAreaFactory(opportunity=opportunity, boundary=square(0, 0))
        new = WorkAreaFactory(opportunity=opportunity, boundary=square(offset, 0))

        assert bool(find_pairs([new])) == overlaps
        if overlaps:
            assert find_pairs([new]) == {(new.pk, existing.pk): 0.06}

    @pytest.mark.parametrize(
        "x, y, size",
        # Places and sizes where an exact 5% measures slightly over, from floating-point noise.
        [(36.8, -1.29, 0.005), (7.4, 9.6, 0.001), (0, 0, 1.0), (77.1, 28.6, 0.001), (151.2, -33.8, 0.001)],
    )
    def test_exactly_the_limit_passes(self, opportunity, x, y, size):
        WorkAreaFactory(opportunity=opportunity, boundary=square(x, y, size))
        new = WorkAreaFactory(opportunity=opportunity, boundary=square(x + 0.95 * size, y, size))

        assert find_pairs([new]) == {}

    def test_shared_corner_passes(self, opportunity):
        WorkAreaFactory(opportunity=opportunity, boundary=square(0, 0))
        new = WorkAreaFactory(opportunity=opportunity, boundary=square(1, 1))

        assert find_pairs([new]) == {}

    def test_ratio_is_measured_against_the_smaller_work_area(self, opportunity):
        large = WorkAreaFactory(opportunity=opportunity, boundary=square(0, 0, 10))
        small_inside = WorkAreaFactory(opportunity=opportunity, boundary=square(1, 1))

        assert find_pairs([small_inside]) == {(small_inside.pk, large.pk): 1.0}

    @pytest.mark.parametrize(
        "status, overlaps",
        [
            (WorkAreaStatus.EXCLUDED, False),
            (WorkAreaStatus.INACCESSIBLE, True),
            (WorkAreaStatus.NOT_VISITED, True),
        ],
    )
    def test_existing_work_area_status(self, opportunity, status, overlaps):
        WorkAreaFactory(opportunity=opportunity, boundary=square(0, 0), status=status)
        new = WorkAreaFactory(opportunity=opportunity, boundary=square(0.5, 0))

        assert bool(find_pairs([new])) == overlaps

    def test_pair_of_new_work_areas_is_reported_once(self, opportunity):
        first = WorkAreaFactory(opportunity=opportunity, boundary=square(0, 0))
        second = WorkAreaFactory(opportunity=opportunity, boundary=square(0.5, 0))

        assert find_pairs([first, second]) == {(first.pk, second.pk): 0.5}

    def test_new_work_area_overlapping_new_and_existing_ones(self, opportunity):
        existing = WorkAreaFactory(opportunity=opportunity, boundary=square(0, 0))
        first = WorkAreaFactory(opportunity=opportunity, boundary=square(0.5, 0))
        second = WorkAreaFactory(opportunity=opportunity, boundary=square(0.75, 0))

        assert find_pairs([first, second]) == {
            (first.pk, existing.pk): 0.5,
            (first.pk, second.pk): 0.75,
            (second.pk, existing.pk): 0.25,
        }

    def test_other_opportunities_are_ignored(self, opportunity):
        WorkAreaFactory(boundary=square(0, 0))
        new = WorkAreaFactory(opportunity=opportunity, boundary=square(0, 0))

        assert find_pairs([new]) == {}

    def test_no_new_work_areas(self, opportunity):
        WorkAreaFactory(opportunity=opportunity, boundary=square(0, 0))

        assert find_pairs([]) == {}

    def test_invalid_existing_boundary_is_measured_as_repaired(self, opportunity):
        # A bow-tie inside the unit square. Unrepaired, ST_Intersection raises on it and ST_Area reads 0.
        bow_tie = WorkAreaFactory(
            opportunity=opportunity,
            boundary=Polygon(((0, 0), (1, 1), (1, 0), (0, 1), (0, 0)), srid=SRID),
        )
        covering = WorkAreaFactory(opportunity=opportunity, boundary=square(-1, -1, 3))

        assert find_pairs([covering]) == {(covering.pk, bow_tie.pk): 1.0}
