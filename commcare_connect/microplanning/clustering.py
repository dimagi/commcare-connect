from collections import defaultdict, deque
from uuid import uuid4

import geopandas as gpd
from shapely import shared_paths, wkb

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
        gdf = self._prepare_data()
        work_area_groups = defaultdict(set)

        for ward, ward_gdf in gdf.groupby("ward"):
            adjacency = self._build_adjacency(ward_gdf)

            ward_gdf = ward_gdf.copy()
            ward_gdf["_cx"] = ward_gdf.centroid.x
            ward_gdf["_cy"] = ward_gdf.centroid.y
            sorted_idx = ward_gdf.sort_values(["_cx", "_cy"], ascending=[True, False]).index
            unvisited = set(ward_gdf.index)

            for idx in sorted_idx:
                if idx not in unvisited:
                    continue

                cluster = self._bfs_cluster(
                    seed_idx=idx,
                    unvisited=unvisited,
                    adjacency=adjacency,
                    ward_gdf=gdf,
                )

                if not cluster:
                    cluster = [idx]
                    unvisited.discard(idx)

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

    def _build_adjacency(self, ward_gdf: gpd.GeoDataFrame, tolerance: float = 1e-6) -> dict:
        adjacency = {idx: [] for idx in ward_gdf.index}
        ward_gdf = ward_gdf.to_crs(3857)

        for work_area_id, row in ward_gdf.iterrows():
            geom = row.geometry

            query_geom = ward_gdf.loc[work_area_id, "geometry"].buffer(self.buffer_distance / 2)
            candidate_idxs = ward_gdf.sindex.query(query_geom, predicate="intersects")

            for i in candidate_idxs:
                neighbour_id = ward_gdf.index[i]
                if neighbour_id == work_area_id:
                    continue

                shared = shared_paths(geom.boundary, ward_gdf.geometry.iloc[i].boundary)
                if shared.length > tolerance:
                    adjacency[work_area_id].append(neighbour_id)
                    continue

                dist = ward_gdf.loc[work_area_id, "geometry"].distance(ward_gdf.geometry.iloc[i])
                if dist <= self.buffer_distance:
                    adjacency[work_area_id].append(neighbour_id)

        return adjacency

    def _bfs_cluster(
        self,
        seed_idx,
        unvisited: set,
        adjacency: dict,
        ward_gdf: gpd.GeoDataFrame,
    ) -> list:
        cluster = []
        total_buildings = 0
        queue = deque([seed_idx])
        seen = {seed_idx}

        while queue:
            current = queue.popleft()

            if current not in unvisited:
                continue

            building_count = ward_gdf.loc[current, "building_count"]

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
        data = []
        for wa in WorkArea.objects.filter(opportunity_id=self.opportunity_id, work_area_group__isnull=True):
            data.append(
                {
                    "id": wa.id,
                    "ward": wa.ward,
                    "centroid": wkb.loads(bytes(wa.centroid.wkb)),
                    "boundary": wkb.loads(bytes(wa.boundary.wkb)),
                    "building_count": wa.building_count,
                }
            )
        work_area_df = gpd.GeoDataFrame(
            columns=["id", "ward", "centroid", "boundary", "building_count"],
            data=data,
            geometry="boundary",
            crs="EPSG:4326",
        )
        work_area_df = work_area_df.rename_geometry("geometry")
        work_area_df["boundary"] = work_area_df.geometry
        work_area_df = work_area_df.set_index("id")
        return work_area_df
