from collections import defaultdict, deque
from uuid import uuid4

from django.db import transaction
from pyproj import Transformer
from shapely import shared_paths, unary_union, wkb
from shapely.ops import transform
from shapely.strtree import STRtree

from commcare_connect.microplanning.models import WorkArea, WorkAreaGroup


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
        max_buildings=300,
        buffer_distance=100,
    ):
        self.opportunity_id = opportunity_id
        self.max_buildings = max_buildings
        self.buffer_distance = buffer_distance
        self.transformer = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)

    def cluster_work_areas(self):
        work_areas = self._prepare_data()
        work_area_groups = defaultdict(set)

        wards = defaultdict(list)
        for wa_id, wa_data in work_areas.items():
            wards[wa_data["ward"]].append(wa_id)

        for ward, ward_ids in wards.items():
            ward_data = {wa_id: work_areas[wa_id] for wa_id in ward_ids}
            adjacency = self._build_adjacency(ward_data)

            # Sort by centroid coordinates (x ascending, y descending)
            sorted_ids = sorted(
                ward_ids,
                key=lambda wa_id: (
                    ward_data[wa_id]["centroid"].x,
                    -ward_data[wa_id]["centroid"].y,
                ),
            )
            unvisited = set(ward_ids)

            for wa_id in sorted_ids:
                if wa_id not in unvisited:
                    continue

                cluster = self._bfs_cluster(
                    seed_idx=wa_id,
                    unvisited=unvisited,
                    adjacency=adjacency,
                    work_areas=work_areas,
                )

                if not cluster:
                    cluster = [wa_id]
                    unvisited.discard(wa_id)

                group_id = str(uuid4())
                work_area_groups[(ward, group_id)].update(cluster)

        with transaction.atomic():
            for key, work_area_ids in work_area_groups.items():
                ward, group_id = key
                combined_work_area_boundary = unary_union([work_areas[wa_id]["boundary"] for wa_id in work_area_ids])
                hull = combined_work_area_boundary.convex_hull
                if hull.geom_type != "Polygon":
                    hull = hull.buffer(1e-6)
                group_boundary = hull.wkt
                group_building_count = sum(work_areas[wa_id]["building_count"] for wa_id in work_area_ids)
                work_area_group = WorkAreaGroup.objects.create(
                    opportunity_id=self.opportunity_id,
                    ward=ward,
                    name=group_id,
                    boundary=group_boundary,
                    building_count=group_building_count,
                )
                WorkArea.objects.filter(
                    id__in=work_area_ids,
                    opportunity=self.opportunity_id,
                    work_area_group__isnull=True,
                ).update(work_area_group=work_area_group)

    def _build_adjacency(self, ward_data: dict, tolerance: float = 1e-6) -> dict:
        adjacency = {wa_id: set() for wa_id in ward_data.keys()}

        transformed_geoms = {}
        for wa_id, wa in ward_data.items():
            transformed_geoms[wa_id] = transform(self.transformer.transform, wa["boundary"])

        wa_ids_list = list(transformed_geoms.keys())
        geometries = [transformed_geoms[wa_id] for wa_id in wa_ids_list]

        spatial_index = STRtree(geometries)

        for work_area_id, geom in transformed_geoms.items():
            query_geom = geom.buffer(self.buffer_distance)
            candidate_indices = spatial_index.query(query_geom, predicate="intersects")

            for idx in candidate_indices:
                neighbour_id = wa_ids_list[idx]
                if neighbour_id == work_area_id:
                    continue

                candidate_geom = transformed_geoms[neighbour_id]

                shared = shared_paths(geom.boundary, candidate_geom.boundary)
                if shared.length > tolerance:
                    adjacency[work_area_id].add(neighbour_id)
                    continue

                dist = geom.distance(candidate_geom)
                if dist <= self.buffer_distance:
                    adjacency[work_area_id].add(neighbour_id)

        return adjacency

    def _bfs_cluster(
        self,
        seed_idx,
        unvisited: set,
        adjacency: dict,
        work_areas: dict,
    ) -> list:
        cluster = []
        total_buildings = 0
        queue = deque([seed_idx])
        seen = {seed_idx}

        while queue:
            current = queue.popleft()

            if current not in unvisited:
                continue

            building_count = work_areas[current]["building_count"]

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
            work_areas[wa.id] = {
                "id": wa.id,
                "ward": wa.ward,
                "centroid": wkb.loads(bytes(wa.centroid.wkb)),
                "boundary": wkb.loads(bytes(wa.boundary.wkb)),
                "building_count": wa.building_count,
            }
        return work_areas
