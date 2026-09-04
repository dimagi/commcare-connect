"""
Building footprints for the microplanning map.

Footprints come from Overture Maps, which is not in our database, so requests are served straight
from the source and cached. To keep that cache useful the requested viewport bbox is snapped
outward to a fixed XYZ grid (``GRID_ZOOM``): raw viewport bboxes are arbitrary floats that never
repeat, while grid tiles are shared between pans and between users looking at the same place.
"""

import mercantile
import shapely
from django.core.cache import cache

from commcare_connect.microplanning.const import (
    BUILDINGS_CACHE_KEY,
    BUILDINGS_CACHE_TIMEOUT,
    GRID_ZOOM,
    MAX_BUILDING_GRID_TILES,
    OVERTURE_BUILDING_TYPE,
    OVERTURE_CONNECT_TIMEOUT,
    OVERTURE_REQUEST_TIMEOUT,
)
from commcare_connect.microplanning.exceptions import AreaTooLarge, BuildingDataUnavailable

# The Overture release footprints are read from. We could probably store this in the DB and read
# it on a weekly basis.
OVERTURE_RELEASE = "2026-08-19.0"

# Web Mercator cannot represent the poles; this is the latitude the projection is cut off at.
MAX_MERCATOR_LATITUDE = 85.0511


def parse_bbox(raw):
    """Parse a ``west,south,east,north`` query parameter, raising ValueError if it is unusable."""
    if not raw:
        raise ValueError("bbox is required")

    parts = raw.split(",")
    if len(parts) != 4:
        raise ValueError("bbox must have four comma-separated values: west,south,east,north")

    try:
        west, south, east, north = (float(part) for part in parts)
    except ValueError:
        raise ValueError("bbox values must be numbers")

    if not (-180 <= west <= 180 and -180 <= east <= 180):
        raise ValueError("bbox longitudes must be between -180 and 180")
    if not (-90 <= south <= 90 and -90 <= north <= 90):
        raise ValueError("bbox latitudes must be between -90 and 90")
    if west >= east or south >= north:
        raise ValueError("bbox must be west,south,east,north with west < east and south < north")

    return west, south, east, north


def buildings_for_bbox(west, south, east, north):
    """
    Return a GeoJSON FeatureCollection of buildings covering the given bbox.

    Its ``bbox`` member is the snapped grid area actually covered, which is always at least the
    requested one.

    Grid tiles are read from the cache, and any that are missing are fetched from Overture together and
    written back, so a pan that shifts the viewport by a grid tile only pays for the new column.

    Raises ``AreaTooLarge`` for a bbox covering more grid tiles than one request may fetch.
    """
    if count_covering_grid_tiles(west, south, east, north) > MAX_BUILDING_GRID_TILES:
        raise AreaTooLarge(f"{west},{south},{east},{north} covers more than {MAX_BUILDING_GRID_TILES} grid_tiles")

    grid_tiles = covering_grid_tiles(west, south, east, north)
    cache_tile_keys = {grid_tile: cache_key_for_grid_tile(grid_tile) for grid_tile in grid_tiles}
    # Read once and pass it on: a second read could miss a grid tile this one hit, and that grid
    # tile is not in `fetched_buildings` precisely because the first read found it.
    cached_buildings = cache.get_many(list(cache_tile_keys.values()))

    missing_grid_tiles = [grid_tile for grid_tile in grid_tiles if cache_tile_keys[grid_tile] not in cached_buildings]
    # `bounds_for_grid_tiles` has no answer for an empty set, and there is nothing to fetch anyway.
    fetched_buildings = fetch_buildings_for_grid_tiles(missing_grid_tiles) if missing_grid_tiles else {}
    cache.set_many(
        {cache_tile_keys[grid_tile]: features for grid_tile, features in fetched_buildings.items()},
        BUILDINGS_CACHE_TIMEOUT,
    )

    features = _dedupe_features_across_grid_tiles(grid_tiles, cache_tile_keys, cached_buildings, fetched_buildings)
    return {"type": "FeatureCollection", "bbox": list(bounds_for_grid_tiles(grid_tiles)), "features": features}


def _dedupe_features_across_grid_tiles(grid_tiles, cache_tile_keys, cached_buildings, fetched_buildings):
    """
    Flatten the per-grid-tile features into one list, in grid tile order, each building once.
    """
    features = []
    seen_ids = set()
    for grid_tile in grid_tiles:
        for feature in cached_buildings.get(cache_tile_keys[grid_tile], fetched_buildings.get(grid_tile, [])):
            # A building straddling a grid tile boundary is stored under both grid tiles.
            feature_id = feature.get("properties", {}).get("id")
            if feature_id is not None:
                if feature_id in seen_ids:
                    continue
                seen_ids.add(feature_id)
            features.append(feature)
    return features


def fetch_buildings_for_grid_tiles(grid_tiles):
    """
    Fetch the given grid tiles from Overture, returning ``{(x, y): [feature, ...]}``.

    Every grid tile asked for is present in the result, empty ones included, so the caller can cache the
    fact that an area has no buildings. One query covers the rectangle enclosing all of them rather
    than one query per grid tile: each Overture read pays seconds of fixed overhead opening the remote
    dataset, and neighbouring grid tiles largely share the parquet row groups that then get scanned.
    """
    try:
        reader = _overture_building_reader(bounds_for_grid_tiles(grid_tiles))
        table = reader.read_all()
    except BuildingDataUnavailable:
        raise
    except Exception as e:
        raise BuildingDataUnavailable("Could not read building data from Overture") from e

    return _features_by_grid_tile(table, grid_tiles)


def _overture_building_reader(bounds):
    # Imported lazily: overturemaps pulls in pyarrow, which is slow and memory-hungry to import,
    # and most requests to this process never ask for buildings.
    from overturemaps import record_batch_reader

    reader = record_batch_reader(
        OVERTURE_BUILDING_TYPE,
        bbox=bounds,
        release=OVERTURE_RELEASE,
        connect_timeout=OVERTURE_CONNECT_TIMEOUT,
        request_timeout=OVERTURE_REQUEST_TIMEOUT,
        stac=True,
    )
    if reader is None:
        # None means STAC found no parquet files intersecting the bbox; Treat it as an
        # error rather than as "no buildings here", or we would cache an empty result for an area
        # we never managed to read.
        raise BuildingDataUnavailable(f"Overture returned no reader for {bounds}")
    return reader


def _features_by_grid_tile(table, grid_tiles):
    """Turn an Overture record batch into GeoJSON features, bucketed by the grid tiles they fall in."""
    by_grid_tile = {grid_tile: [] for grid_tile in grid_tiles}
    wanted = set(grid_tiles)

    ids = table.column("id").to_pylist()
    geometries = shapely.from_wkb(table.column("geometry").to_pylist())
    bounds = table.column("bbox").to_pylist()

    for feature_id, geometry, feature_bounds in zip(ids, geometries, bounds):
        if geometry is None:
            continue
        # A building may overlap several grid tiles, and may reach outside the ones we asked for: the
        # query covers a rectangle enclosing them, and buildings on its edge stick out. Only the
        # grid tiles we are about to cache are filled in.
        overlapped = [
            grid_tile
            for grid_tile in covering_grid_tiles(
                feature_bounds["xmin"], feature_bounds["ymin"], feature_bounds["xmax"], feature_bounds["ymax"]
            )
            if grid_tile in wanted
        ]
        if not overlapped:
            continue

        feature = {
            "type": "Feature",
            "geometry": shapely.geometry.mapping(geometry),
            "properties": {"id": feature_id},
        }
        for grid_tile in overlapped:
            by_grid_tile[grid_tile].append(feature)

    return by_grid_tile


def covering_grid_tiles(west, south, east, north, zoom=GRID_ZOOM):
    """
    Return the ``(x, y)`` grid tiles at ``zoom`` that together cover the given bbox.
    """
    grid_tiles = [(tile.x, tile.y) for tile in mercantile.tiles(west, south, east, north, zoom, truncate=True)]
    if grid_tiles:
        return grid_tiles

    corner = mercantile.tile(west, min(north, MAX_MERCATOR_LATITUDE), zoom, truncate=True)
    return [(corner.x, corner.y)]


def count_covering_grid_tiles(west, south, east, north, zoom=GRID_ZOOM):
    """
    How many grid tiles ``covering_grid_tiles`` would return, without building the list.
    """
    # mercantile.tile cannot project the poles, and clamps nothing itself. mercantile.tiles does
    # this for us; here it is ours to do.
    north = min(north, MAX_MERCATOR_LATITUDE)
    south = max(south, -MAX_MERCATOR_LATITUDE)

    top_left = mercantile.tile(west, north, zoom, truncate=True)
    bottom_right = mercantile.tile(east - mercantile.LL_EPSILON, south + mercantile.LL_EPSILON, zoom, truncate=True)

    # A bbox smaller than one grid tile, or landing exactly on a boundary, still covers it.
    return (max(bottom_right.x - top_left.x, 0) + 1) * (max(bottom_right.y - top_left.y, 0) + 1)


def bounds_for_grid_tiles(grid_tiles, zoom=GRID_ZOOM):
    """
    Return the ``(west, south, east, north)`` bounds enclosing every grid tile in ``grid_tiles``.
    """
    corners = [mercantile.bounds(x, y, zoom) for x, y in grid_tiles]
    return (
        min(west for west, _, _, _ in corners),
        min(south for _, south, _, _ in corners),
        max(east for _, _, east, _ in corners),
        max(north for _, _, _, north in corners),
    )


def cache_key_for_grid_tile(grid_tile, zoom=GRID_ZOOM):
    x, y = grid_tile
    return BUILDINGS_CACHE_KEY.format(release=OVERTURE_RELEASE, z=zoom, x=x, y=y)
