import logging
from collections import defaultdict, deque
from dataclasses import dataclass

from django.db import transaction
from pyproj import Transformer
from shapely import get_dimensions, wkb
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform
from shapely.strtree import STRtree

from commcare_connect.microplanning.models import WorkArea, WorkAreaGroup

logger = logging.getLogger(__name__)

# Small buffer (in meters) applied to degenerate convex hulls
# (e.g. LineString, Point) to ensure they become valid Polygons.
DEGENERATE_HULL_BUFFER = 1e-6


@dataclass
class WorkAreaData:
    ward: str
    centroid: BaseGeometry
    boundary: BaseGeometry
    building_count: int


class WorkAreaGrouper:
    """
    This class uses a spatial clustering algorithm to combine neighboring work areas into
    groups. The grouping process:

    1. Separates work areas by ward (wards are never mixed in groups)
    2. Identifies adjacent work areas using shared boundaries or proximity
    3. Uses breadth-first search (BFS) to form spatially contiguous clusters
    4. Respects a maximum building count per cluster
    5. Creates WorkAreaGroup objects and assigns work areas to them


    Adjacency Detection:
    - Two work areas are considered adjacent if they share a boundary
    - OR if they are within buffer_distance meters of each other
    - Adjacency is computed in EPSG:3857 (Web Mercator) for approximate distance calculations.
      Web Mercator is not an equidistant projection, so buffer_distance is approximate.

    Clustering Strategy:
    - Work areas are processed in a deterministic order (sorted by centroid coordinates)
    - Starting from each unvisited area, a BFS expands to adjacent areas
    - Areas are added to the cluster if they don't exceed max_buildings
    - Each cluster becomes a WorkAreaGroup

    Args:
        opportunity_id:  The ID of the opportunity whose work areas should be grouped
        max_buildings:   Maximum total building count allowed per work area group.
                         Default is 300.
        buffer_distance: Distance in meters to consider work areas as adjacent even if
                         they don't share a boundary. Default is 100 meters. This helps
                         connect work areas that are close but separated by small gaps.

    Note:
        - Only work areas without an existing work_area_group are processed
        - Work areas from different wards are never grouped together
        - If a single work area's building_count exceeds max_buildings, it is still
          placed in its own group (the constraint cannot be satisfied in this case)
    """

    def __init__(
        self,
        opportunity_id: int,
        max_buildings: int = 300,
        buffer_distance: int = 100,
    ):
        self.opportunity_id = opportunity_id
        self.max_buildings = max_buildings
        self.buffer_distance = buffer_distance
        self.transformer = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)

    def cluster_work_areas(self):
        work_areas = self._prepare_data()
        if not work_areas:
            logger.info("Opportunity %s: no ungrouped work areas to cluster", self.opportunity_id)
            return

        work_area_groups = defaultdict(set)
        group_index = 0

        wards = defaultdict(list)
        for wa_id, wa_data in work_areas.items():
            wards[wa_data.ward].append(wa_id)

        logger.info(
            "Opportunity %s: clustering %d work areas across %d wards (max_buildings=%d, buffer_distance=%d)",
            self.opportunity_id,
            len(work_areas),
            len(wards),
            self.max_buildings,
            self.buffer_distance,
        )

        for ward, ward_ids in wards.items():
            ward_data = {wa_id: work_areas[wa_id] for wa_id in ward_ids}
            adjacency = self._build_adjacency(ward_data)

            # Sort by centroid coordinates (x ascending, y descending)
            sorted_ids = sorted(
                ward_ids,
                key=lambda wa_id: (
                    ward_data[wa_id].centroid.x,
                    -ward_data[wa_id].centroid.y,
                ),
            )
            unvisited = set(ward_ids)
            ward_group_count = 0

            for wa_id in sorted_ids:
                if wa_id not in unvisited:
                    continue

                cluster = self._bfs_cluster(
                    seed_id=wa_id,
                    unvisited=unvisited,
                    adjacency=adjacency,
                    work_areas=work_areas,
                )

                if not cluster:
                    cluster = [wa_id]
                    unvisited.discard(wa_id)
                    logger.debug(
                        "Opportunity %s, ward %s: work area %s exceeds max_buildings, assigned to its own group",
                        self.opportunity_id,
                        ward,
                        wa_id,
                    )

                group_index += 1
                group_name = f"{ward}_{group_index}"
                work_area_groups[(ward, group_name)].update(cluster)
                ward_group_count += 1

            logger.info(
                "Opportunity %s, ward %s: %d work areas clustered into %d groups",
                self.opportunity_id,
                ward,
                len(ward_ids),
                ward_group_count,
            )

        with transaction.atomic():
            for key, work_area_ids in work_area_groups.items():
                ward, group_id = key
                work_area_group = WorkAreaGroup.objects.create(
                    opportunity_id=self.opportunity_id,
                    ward=ward,
                    name=group_id,
                )
                WorkArea.objects.filter(
                    id__in=work_area_ids,
                    opportunity=self.opportunity_id,
                    work_area_group__isnull=True,
                ).update(work_area_group=work_area_group)

        logger.info(
            "Opportunity %s: clustering complete, created %d groups",
            self.opportunity_id,
            len(work_area_groups),
        )

    def _build_adjacency(self, ward_data: dict) -> dict:
        adjacency = {wa_id: set() for wa_id in ward_data.keys()}
        distances = {}

        transformed_geoms = {}
        for wa_id, wa in ward_data.items():
            transformed_geoms[wa_id] = transform(self.transformer.transform, wa.boundary)

        wa_ids_list = list(transformed_geoms.keys())
        spatial_index = STRtree([transformed_geoms[wa_id] for wa_id in wa_ids_list])

        for work_area_id, geom in transformed_geoms.items():
            query_geom = geom.buffer(self.buffer_distance)
            candidate_indices = spatial_index.query(query_geom, predicate="intersects")

            for idx in candidate_indices:
                neighbour_id = wa_ids_list[idx]
                if neighbour_id == work_area_id or neighbour_id in adjacency[work_area_id]:
                    continue

                candidate_geom = transformed_geoms[neighbour_id]
                shared_edge = geom.intersection(candidate_geom)
                dist = geom.distance(candidate_geom)

                if get_dimensions(shared_edge) >= 1 or dist <= self.buffer_distance:
                    adjacency[work_area_id].add(neighbour_id)
                    adjacency[neighbour_id].add(work_area_id)
                    pair = (min(work_area_id, neighbour_id), max(work_area_id, neighbour_id))
                    distances[pair] = dist

        def _dist(wa_id, neighbour_id):
            pair = (min(wa_id, neighbour_id), max(wa_id, neighbour_id))
            return distances[pair]

        return {wa_id: sorted(neighbours, key=lambda n: _dist(wa_id, n)) for wa_id, neighbours in adjacency.items()}

    def _bfs_cluster(
        self,
        seed_id,
        unvisited: set,
        adjacency: dict,
        work_areas: dict,
    ) -> list:
        cluster = []
        total_buildings = 0
        queue = deque([seed_id])
        seen = {seed_id}

        while queue:
            current = queue.popleft()

            if current not in unvisited:
                continue

            building_count = work_areas[current].building_count

            if total_buildings + building_count > self.max_buildings:
                seen.discard(current)
                continue

            cluster.append(current)
            unvisited.discard(current)
            total_buildings += building_count

            for neighbour in adjacency.get(current, []):
                if neighbour in unvisited and neighbour not in seen:
                    queue.append(neighbour)
                    seen.add(neighbour)

        return cluster

    def _prepare_data(self):
        work_areas = {}
        work_area_qs = WorkArea.objects.filter(
            opportunity_id=self.opportunity_id,
            work_area_group__isnull=True,
            building_count__gt=0,
        )
        for wa in work_area_qs.iterator():
            work_areas[wa.id] = WorkAreaData(
                ward=wa.ward,
                centroid=wkb.loads(bytes(wa.centroid.wkb)),
                boundary=wkb.loads(bytes(wa.boundary.wkb)),
                building_count=wa.building_count,
            )
        return work_areas
