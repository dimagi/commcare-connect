from collections import defaultdict, deque
from uuid import uuid4

from pyproj import Transformer
from shapely import shared_paths, wkb
from shapely.strtree import STRtree

from commcare_connect.microplanning.models import WorkArea, WorkAreaGroup


class WorkAreaGrouper:
    def __init__(
        self,
        opportunity_id: int,
        max_buildings=300,
        buffer_distance=100,
    ):
        self.opportunity_id = opportunity_id
        self.max_buildings = max_buildings
        self.buffer_distance = buffer_distance

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

        for key, work_area_ids in work_area_groups.items():
            ward, group_id = key
            work_area_group = WorkAreaGroup.objects.create(
                opportunity_id=self.opportunity_id, ward=ward, name=group_id
            )
            WorkArea.objects.filter(
                id__in=work_area_ids,
                opportunity=self.opportunity_id,
                work_area_group__isnull=True,
            ).update(work_area_group=work_area_group)

    def _build_adjacency(self, ward_data: dict, tolerance: float = 1e-6) -> dict:
        adjacency = {wa_id: [] for wa_id in ward_data.keys()}

        transformer = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)

        transformed_geoms = {}
        for wa_id, wa in ward_data.items():
            transformed_geoms[wa_id] = self._transform_geometry(wa["geometry"], transformer)

        wa_ids_list = list(transformed_geoms.keys())
        geometries = [transformed_geoms[wa_id] for wa_id in wa_ids_list]

        spatial_index = STRtree(geometries)

        for work_area_id, geom in transformed_geoms.items():
            query_geom = geom.buffer(self.buffer_distance / 2)
            candidate_indices = spatial_index.query(query_geom, predicate="intersects")

            for idx in candidate_indices:
                neighbour_id = wa_ids_list[idx]
                if neighbour_id == work_area_id:
                    continue

                candidate_geom = transformed_geoms[neighbour_id]

                shared = shared_paths(geom.boundary, candidate_geom.boundary)
                if shared.length > tolerance:
                    adjacency[work_area_id].append(neighbour_id)
                    continue

                dist = geom.distance(candidate_geom)
                if dist <= self.buffer_distance:
                    adjacency[work_area_id].append(neighbour_id)

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
        for wa in WorkArea.objects.filter(opportunity_id=self.opportunity_id, work_area_group__isnull=True):
            work_areas[wa.id] = {
                "id": wa.id,
                "ward": wa.ward,
                "centroid": wkb.loads(bytes(wa.centroid.wkb)),
                "geometry": wkb.loads(bytes(wa.boundary.wkb)),
                "building_count": wa.building_count,
            }
        return work_areas

    def _transform_geometry(self, geom, transformer):
        from shapely.ops import transform

        return transform(transformer.transform, geom)
