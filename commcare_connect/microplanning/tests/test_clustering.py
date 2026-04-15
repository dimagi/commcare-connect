import pytest
from django.contrib.gis.geos import Point, Polygon

from commcare_connect.microplanning.clustering import WorkAreaGrouper
from commcare_connect.microplanning.models import SRID, WorkAreaGroup
from commcare_connect.microplanning.tests.factories import WorkAreaFactory


@pytest.mark.django_db
class TestWorkAreaGrouper:
    def create_adjacent_work_areas(
        self,
        opportunity,
        ward,
        start_x=77.0,
        start_y=28.0,
        size=0.01,
        slug_prefix="area",
    ):
        work_areas = []

        # Create a 2x2 grid of adjacent work areas
        positions = [
            (start_x, start_y),  # Bottom-left
            (start_x + size, start_y),  # Bottom-right
            (start_x, start_y + size),  # Top-left
            (start_x + size, start_y + size),  # Top-right
        ]

        for idx, (x, y) in enumerate(positions):
            boundary = Polygon(
                (
                    (x, y),
                    (x + size, y),
                    (x + size, y + size),
                    (x, y + size),
                    (x, y),
                ),
                srid=SRID,
            )
            centroid = Point(x + size / 2, y + size / 2, srid=SRID)

            work_area = WorkAreaFactory(
                opportunity=opportunity,
                slug=f"{slug_prefix}-{ward}-{idx}",
                ward=ward,
                centroid=centroid,
                boundary=boundary,
                building_count=50,  # Small enough to fit multiple in one group
            )
            work_areas.append(work_area)

        return work_areas

    def test_cluster_adjacent_work_areas(self, opportunity):
        work_areas = self.create_adjacent_work_areas(opportunity, ward="ward-1")

        grouper = WorkAreaGrouper(opportunity_id=opportunity.id, max_buildings=300)
        grouper.cluster_work_areas()

        work_area_groups = WorkAreaGroup.objects.filter(opportunity=opportunity)

        assert work_area_groups.exists()
        assert work_area_groups.count() == 1
        for work_area in work_areas:
            work_area.refresh_from_db()
            assert work_area.work_area_group_id == work_area_groups[0].id

    def test_cluster_respects_max_buildings(self, opportunity):
        work_areas = self.create_adjacent_work_areas(opportunity, ward="ward-1")
        for wa in work_areas:
            wa.building_count = 100
            wa.save()

        grouper = WorkAreaGrouper(opportunity_id=opportunity.id, max_buildings=150)
        grouper.cluster_work_areas()

        work_area_groups = WorkAreaGroup.objects.filter(opportunity=opportunity)

        assert work_area_groups.count() == 4
        for group in work_area_groups:
            assert group.building_count <= 150

    def test_cluster_multiple_wards_separately(self, opportunity):
        # Create work areas in two different wards
        ward1_areas = self.create_adjacent_work_areas(opportunity, ward="ward-1", start_x=77.0, slug_prefix="w1")
        ward2_areas = self.create_adjacent_work_areas(opportunity, ward="ward-2", start_x=78.0, slug_prefix="w2")

        grouper = WorkAreaGrouper(opportunity_id=opportunity.id, max_buildings=300)
        grouper.cluster_work_areas()

        work_area_groups = WorkAreaGroup.objects.filter(opportunity=opportunity)

        assert work_area_groups.count() >= 2

        wards = {wag.ward for wag in work_area_groups}
        assert "ward-1" in wards
        assert "ward-2" in wards

        ward1_group = WorkAreaGroup.objects.get(opportunity=opportunity, ward="ward-1")
        ward2_group = WorkAreaGroup.objects.get(opportunity=opportunity, ward="ward-2")

        for work_area in ward1_areas:
            work_area.refresh_from_db()
            assert work_area.work_area_group == ward1_group

        for work_area in ward2_areas:
            work_area.refresh_from_db()
            assert work_area.work_area_group == ward2_group

    def test_cluster_empty_opportunity(self, opportunity):
        grouper = WorkAreaGrouper(opportunity_id=opportunity.id)
        grouper.cluster_work_areas()
        assert not WorkAreaGroup.objects.filter(opportunity=opportunity).exists()

    def test_cluster_single_work_area(self, opportunity):
        work_area = WorkAreaFactory(
            opportunity=opportunity,
            slug="area-1",
            ward="ward-1",
            centroid=Point(77.5, 28.5, srid=SRID),
            boundary=Polygon(
                ((77.0, 28.0), (78.0, 28.0), (78.0, 29.0), (77.0, 29.0), (77.0, 28.0)),
                srid=SRID,
            ),
            building_count=100,
        )

        grouper = WorkAreaGrouper(opportunity_id=opportunity.id)
        grouper.cluster_work_areas()

        work_area_groups = WorkAreaGroup.objects.filter(opportunity=opportunity)

        assert work_area_groups.count() == 1
        work_area.refresh_from_db()
        assert work_area.work_area_group == work_area_groups[0]

    def test_cluster_idempotent(self, opportunity):
        self.create_adjacent_work_areas(opportunity, ward="ward-1")

        grouper = WorkAreaGrouper(opportunity_id=opportunity.id, max_buildings=300)
        grouper.cluster_work_areas()

        assert WorkAreaGroup.objects.filter(opportunity=opportunity).count() == 1

        # Running again should be a no-op
        grouper.cluster_work_areas()

        assert WorkAreaGroup.objects.filter(opportunity=opportunity).count() == 1

    def test_cluster_excludes_zero_building_count(self, opportunity):
        work_area = WorkAreaFactory(
            opportunity=opportunity,
            slug="zero-buildings",
            ward="ward-1",
            centroid=Point(77.5, 28.5, srid=SRID),
            boundary=Polygon(
                ((77.0, 28.0), (78.0, 28.0), (78.0, 29.0), (77.0, 29.0), (77.0, 28.0)),
                srid=SRID,
            ),
            building_count=0,
        )

        grouper = WorkAreaGrouper(opportunity_id=opportunity.id)
        grouper.cluster_work_areas()

        assert not WorkAreaGroup.objects.filter(opportunity=opportunity).exists()
        work_area.refresh_from_db()
        assert work_area.work_area_group is None

    def test_cluster_corner_sharing_work_areas(self, opportunity):
        """Work areas sharing only a corner (point) should be grouped together
        because their distance is 0, which is within the default buffer_distance."""
        size = 0.01
        # Create two squares that touch only at a corner point
        wa1 = WorkAreaFactory(
            opportunity=opportunity,
            slug="corner-1",
            ward="ward-1",
            centroid=Point(77.0 + size / 2, 28.0 + size / 2, srid=SRID),
            boundary=Polygon(
                ((77.0, 28.0), (77.0 + size, 28.0), (77.0 + size, 28.0 + size), (77.0, 28.0 + size), (77.0, 28.0)),
                srid=SRID,
            ),
            building_count=50,
        )
        wa2 = WorkAreaFactory(
            opportunity=opportunity,
            slug="corner-2",
            ward="ward-1",
            centroid=Point(77.0 + size + size / 2, 28.0 + size + size / 2, srid=SRID),
            boundary=Polygon(
                (
                    (77.0 + size, 28.0 + size),
                    (77.0 + 2 * size, 28.0 + size),
                    (77.0 + 2 * size, 28.0 + 2 * size),
                    (77.0 + size, 28.0 + 2 * size),
                    (77.0 + size, 28.0 + size),
                ),
                srid=SRID,
            ),
            building_count=50,
        )

        grouper = WorkAreaGrouper(opportunity_id=opportunity.id, max_buildings=300)
        grouper.cluster_work_areas()

        groups = WorkAreaGroup.objects.filter(opportunity=opportunity)
        assert groups.count() == 1

        wa1.refresh_from_db()
        wa2.refresh_from_db()
        assert wa1.work_area_group == wa2.work_area_group

    def test_cluster_single_work_area_exceeding_max_buildings(self, opportunity):
        work_area = WorkAreaFactory(
            opportunity=opportunity,
            slug="large-area",
            ward="ward-1",
            centroid=Point(77.5, 28.5, srid=SRID),
            boundary=Polygon(
                ((77.0, 28.0), (78.0, 28.0), (78.0, 29.0), (77.0, 29.0), (77.0, 28.0)),
                srid=SRID,
            ),
            building_count=500,
        )

        grouper = WorkAreaGrouper(opportunity_id=opportunity.id, max_buildings=300)
        grouper.cluster_work_areas()

        # Should still create a group for the oversized work area
        group = WorkAreaGroup.objects.get(opportunity=opportunity)
        assert group.building_count == 500
        work_area.refresh_from_db()
        assert work_area.work_area_group == group
