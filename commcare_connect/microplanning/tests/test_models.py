from __future__ import annotations

import pytest
from django.contrib.gis.geos import Point, Polygon

from commcare_connect.microplanning.models import SRID, WorkAreaStatus
from commcare_connect.microplanning.tests.factories import (
    WorkAreaFactory,
    WorkAreaGroupFactory,
)
from commcare_connect.opportunity.models import VisitValidationStatus
from commcare_connect.opportunity.tests.factories import OpportunityAccessFactory, UserVisitFactory

APPROVED = VisitValidationStatus.approved
PENDING = VisitValidationStatus.pending
REJECTED = VisitValidationStatus.rejected


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


@pytest.mark.django_db
class TestWorkAreaUpdateStatus:
    """Worker A previously held the area; worker B holds it now."""

    @pytest.fixture
    def access_a(self, opportunity):
        return OpportunityAccessFactory(opportunity=opportunity)

    @pytest.fixture
    def access_b(self, opportunity):
        return OpportunityAccessFactory(opportunity=opportunity)

    def _area(self, opportunity, access_b, status, expected_visit_count):
        return WorkAreaFactory(
            opportunity=opportunity,
            opportunity_access=access_b,
            status=status,
            expected_visit_count=expected_visit_count,
        )

    def _visit(self, work_area, access, status):
        return UserVisitFactory(
            opportunity=work_area.opportunity,
            opportunity_access=access,
            user=access.user,
            work_area=work_area,
            status=status,
        )

    @pytest.mark.parametrize(
        "visits_a, visits_b, evc, initial_status, expected_status",
        [
            pytest.param(
                [APPROVED],
                [APPROVED],
                2,
                WorkAreaStatus.VISITED,
                WorkAreaStatus.EXPECTED_VISIT_REACHED,
                id="handoff-reaches-target-together",
            ),
            pytest.param(
                [PENDING],
                [],
                2,
                WorkAreaStatus.NOT_VISITED,
                WorkAreaStatus.VISITED,
                id="previous-worker-visit-marks-visited",
            ),
            pytest.param(
                [APPROVED, APPROVED],
                [],
                2,
                WorkAreaStatus.VISITED,
                WorkAreaStatus.EXPECTED_VISIT_REACHED,
                id="previous-worker-alone-reaches-target",
            ),
            pytest.param(
                [APPROVED],
                [REJECTED],
                2,
                WorkAreaStatus.VISITED,
                WorkAreaStatus.VISITED,
                id="rejected-visits-do-not-count",
            ),
            pytest.param(
                [APPROVED, APPROVED, APPROVED],
                [],
                0,
                WorkAreaStatus.NOT_VISITED,
                WorkAreaStatus.VISITED,
                id="no-target-never-reached",
            ),
            pytest.param(
                [APPROVED, APPROVED],
                [],
                2,
                WorkAreaStatus.REQUEST_FOR_INACCESSIBLE,
                WorkAreaStatus.REQUEST_FOR_INACCESSIBLE,
                id="untracked-status-unchanged",
            ),
            pytest.param(
                [],
                [],
                2,
                WorkAreaStatus.EXPECTED_VISIT_REACHED,
                WorkAreaStatus.EXPECTED_VISIT_REACHED,
                id="no-visits-keeps-status",
            ),
            pytest.param(
                [APPROVED],
                [APPROVED],
                3,
                WorkAreaStatus.EXPECTED_VISIT_REACHED,
                WorkAreaStatus.VISITED,
                id="raised-target-moves-back-to-visited",
            ),
        ],
    )
    def test_counts_visits_from_all_workers(
        self, opportunity, access_a, access_b, visits_a, visits_b, evc, initial_status, expected_status
    ):
        work_area = self._area(opportunity, access_b, initial_status, evc)
        for status in visits_a:
            self._visit(work_area, access_a, status)
        for status in visits_b:
            self._visit(work_area, access_b, status)

        work_area.update_status()

        work_area.refresh_from_db()
        assert work_area.status == expected_status

    def test_status_change_is_attributed_to_current_worker(self, opportunity, access_a, access_b):
        work_area = self._area(opportunity, access_b, WorkAreaStatus.NOT_VISITED, 1)
        self._visit(work_area, access_a, APPROVED)

        work_area.update_status()

        event = work_area.expected_visit_count_work_area_group_status_opportunity_access_excluded_reason_events.last()
        assert event.status == WorkAreaStatus.EXPECTED_VISIT_REACHED
        assert event.pgh_context.metadata["username"] == access_b.user.username
