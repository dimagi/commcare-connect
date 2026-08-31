from unittest.mock import Mock, patch

import mercantile
import pyarrow as pa
import pytest
import shapely

from commcare_connect.microplanning.buildings import (
    _features_by_grid_tile,
    bounds_for_grid_tiles,
    buildings_for_bbox,
    cache_key_for_grid_tile,
    count_covering_grid_tiles,
    covering_grid_tiles,
    fetch_buildings_for_grid_tiles,
    parse_bbox,
)
from commcare_connect.microplanning.const import (
    BUILDING_MIN_ZOOM,
    GRID_ZOOM,
    MAX_BUILDING_GRID_TILES,
)
from commcare_connect.microplanning.exceptions import AreaTooLarge, BuildingDataUnavailable

# The largest map we draw, in css pixels, and where those numbers come from. They live here rather
# than in const.py because they describe the browser, not the endpoint: the cap is a limit on what
# the server will fetch, and this is only the check that the limit leaves room for a real viewport.
# Sizes follow #map-wrapper in microplanning/home.html - a w-96 sidebar plus its gap beside the map,
# and 300px of page above it.
LARGEST_SCREEN_PX = (3840, 2160)  # a 4K display
SIDEBAR_PX = 384 + 32
PAGE_CHROME_PX = 300
MAPBOX_TILE_PX = 512  # Mapbox GL serves 512px tiles, so the world is this * 2**zoom px across


def _arrow_table(rows):
    """A minimal stand-in for an Overture record batch: id, WKB geometry and a bbox struct."""
    return pa.table(
        {
            "id": pa.array([feature_id for feature_id, _ in rows], pa.string()),
            "geometry": pa.array([shapely.to_wkb(geometry) for _, geometry in rows], pa.binary()),
            "bbox": pa.array(
                [dict(zip(("xmin", "ymin", "xmax", "ymax"), geometry.bounds)) for _, geometry in rows],
                pa.struct([(name, pa.float64()) for name in ("xmin", "ymin", "xmax", "ymax")]),
            ),
        }
    )


def building(feature_id, lon=8.65, lat=9.05):
    return {
        "type": "Feature",
        "geometry": {"type": "Polygon", "coordinates": [[[lon, lat], [lon, lat], [lon, lat], [lon, lat]]]},
        "properties": {"id": feature_id},
    }


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("8.65,9.05,8.70,9.09", (8.65, 9.05, 8.70, 9.09)),
        ("-1.5,-2.5,3,4", (-1.5, -2.5, 3.0, 4.0)),
    ],
)
def test_parse_bbox_accepts_valid_values(raw, expected):
    assert parse_bbox(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "8.65,9.05,8.70",
        "8.65,9.05,8.70,9.09,1",
        "8.65,9.05,8.70,north",
        "-181,9.05,8.70,9.09",
        "8.65,-91,8.70,9.09",
        "8.70,9.05,8.65,9.09",  # west past east
        "8.65,9.09,8.70,9.05",  # south past north
        "8.65,9.05,8.65,9.09",  # zero width
    ],
)
def test_parse_bbox_rejects_bad_values(raw):
    with pytest.raises(ValueError):
        parse_bbox(raw)


def test_covering_grid_tiles_returns_a_single_grid_tile_for_a_bbox_inside_one():
    # A hundred metres or so either side of the origin, well inside one z14 grid tile.
    assert covering_grid_tiles(0.001, 0.001, 0.002, 0.002) == [(8192, 8191)]


def test_covering_grid_tiles_spans_every_grid_tile_a_bbox_touches():
    # Straddles the prime meridian and the equator, so it covers the four grid tiles that meet there.
    assert set(covering_grid_tiles(-0.01, -0.01, 0.01, 0.01)) == {
        (8191, 8191),
        (8191, 8192),
        (8192, 8191),
        (8192, 8192),
    }


def test_covering_grid_tiles_clamps_to_the_world_at_zoom_zero():
    assert covering_grid_tiles(-180, -85, 180, 85, zoom=0) == [(0, 0)]


@pytest.mark.parametrize(
    "bbox",
    [
        (8.65, 9.05, 8.70, 9.09),  # a zoom-15-ish viewport in northern Nigeria
        (-0.01, -0.01, 0.01, 0.01),
        (0.001, 0.001, 0.002, 0.002),
        (-73.99, 40.70, -73.96, 40.73),
    ],
)
def test_covered_area_always_contains_the_requested_bbox(bbox):
    west, south, east, north = bbox
    covered_west, covered_south, covered_east, covered_north = bounds_for_grid_tiles(covering_grid_tiles(*bbox))

    assert covered_west <= west
    assert covered_south <= south
    assert covered_east >= east
    assert covered_north >= north


@pytest.mark.parametrize(
    "bbox",
    [
        (8.65, 9.05, 8.70, 9.09),  # a zoom-15-ish viewport
        (8.6501, 9.0501, 8.6502, 9.0502),  # smaller than one grid tile
        # Narrower than the epsilon mercantile.tiles trims off the closing edges, and sitting on a
        # grid tile boundary, which is the case that made the list come back empty while the count said
        # one - leaving buildings_for_bbox with no grid tiles to take bounds from.
        (0.0, 0.0, 1e-13, 1e-13),
        (-0.01, -0.01, 0.01, 0.01),  # across the prime meridian and the equator
        (3.0, 4.0, 14.0, 14.0),  # far larger than the cap allows
    ],
)
def test_counting_grid_tiles_agrees_with_listing_them(bbox):
    assert count_covering_grid_tiles(*bbox) == len(covering_grid_tiles(*bbox))


def test_a_bbox_narrower_than_the_tile_epsilon_still_covers_its_corner_grid_tile():
    bbox = (0.0, 0.0, 1e-13, 1e-13)

    corner = mercantile.tile(0.0, 0.0, GRID_ZOOM)

    assert covering_grid_tiles(*bbox) == [(corner.x, corner.y)]
    assert count_covering_grid_tiles(*bbox) == 1


def test_counting_grid_tiles_is_cheap_for_a_bbox_that_could_never_be_served():
    """
    parse_bbox admits the whole world, and the view counts grid tiles before anything else, so counting
    must not go anywhere near building the list: this bbox covers 2**28 grid tiles, tens of GB of tuples.
    """
    whole_grid = 2**GRID_ZOOM * 2**GRID_ZOOM

    assert count_covering_grid_tiles(-180, -90, 180, 90) == whole_grid
    assert whole_grid > MAX_BUILDING_GRID_TILES


def test_an_area_past_the_cap_is_refused_before_anything_is_fetched(local_cache):
    with patch("commcare_connect.microplanning.buildings.fetch_buildings_for_grid_tiles") as fetch:
        with pytest.raises(AreaTooLarge):
            buildings_for_bbox(3.0, 4.0, 14.0, 14.0)

    fetch.assert_not_called()


def test_the_cap_admits_the_largest_viewport_we_support():
    assert count_covering_grid_tiles(*_viewport_bbox(*LARGEST_SCREEN_PX, BUILDING_MIN_ZOOM)) <= MAX_BUILDING_GRID_TILES


def _viewport_bbox(screen_width_px, screen_height_px, zoom, west=8.65, south=9.05):
    """
    The bbox the map would ask for on a screen of this size, at this zoom.

    Degrees per pixel is taken at the equator, where a pixel spans the most longitude, and the
    fetched area is snapped outward to whole grid tiles, so this is the worst case for the count.
    """
    width_px = screen_width_px - SIDEBAR_PX
    height_px = screen_height_px - PAGE_CHROME_PX
    degrees_per_px = 360 / (MAPBOX_TILE_PX * 2**zoom)
    return (west, south, west + width_px * degrees_per_px, south + height_px * degrees_per_px)


def test_buildings_for_bbox_returns_a_feature_collection_covering_the_request(local_cache):
    bbox = (8.65, 9.05, 8.70, 9.09)
    with patch(
        "commcare_connect.microplanning.buildings.fetch_buildings_for_grid_tiles",
        side_effect=lambda grid_tiles: {
            grid_tile: [building(f"{grid_tile[0]}-{grid_tile[1]}")] for grid_tile in grid_tiles
        },
    ):
        collection = buildings_for_bbox(*bbox)

    assert collection["type"] == "FeatureCollection"
    assert len(collection["features"]) == len(covering_grid_tiles(*bbox))
    covered_west, covered_south, covered_east, covered_north = collection["bbox"]
    assert covered_west <= bbox[0] and covered_south <= bbox[1]
    assert covered_east >= bbox[2] and covered_north >= bbox[3]


def test_buildings_for_bbox_dedupes_a_building_returned_by_several_grid_tiles(local_cache):
    # A building straddling a grid tile boundary comes back from every grid tile it touches.
    with patch(
        "commcare_connect.microplanning.buildings.fetch_buildings_for_grid_tiles",
        side_effect=lambda grid_tiles: {
            grid_tile: [building("straddler"), building(f"{grid_tile[0]}-{grid_tile[1]}")] for grid_tile in grid_tiles
        },
    ):
        collection = buildings_for_bbox(-0.01, -0.01, 0.01, 0.01)

    ids = [feature["properties"]["id"] for feature in collection["features"]]
    assert ids.count("straddler") == 1
    assert len(ids) == len(set(ids)) == 5  # one straddler plus one per grid tile


def test_buildings_for_bbox_serves_cached_grid_tiles_without_calling_the_service(local_cache):
    bbox = (0.001, 0.001, 0.002, 0.002)
    local_cache.set(cache_key_for_grid_tile(covering_grid_tiles(*bbox)[0]), [building("cached")])

    with patch("commcare_connect.microplanning.buildings.fetch_buildings_for_grid_tiles") as fetch:
        collection = buildings_for_bbox(*bbox)

    fetch.assert_not_called()
    assert [feature["properties"]["id"] for feature in collection["features"]] == ["cached"]


@pytest.mark.parametrize(
    "fetched_ids",
    [
        ["fetched"],
        # An area with nothing in it is worth remembering, or every view of it re-queries Overture.
        [],
    ],
)
def test_buildings_for_bbox_caches_what_it_fetched(local_cache, fetched_ids):
    bbox = (0.001, 0.001, 0.002, 0.002)
    grid_tile = covering_grid_tiles(*bbox)[0]

    with patch(
        "commcare_connect.microplanning.buildings.fetch_buildings_for_grid_tiles",
        side_effect=lambda grid_tiles: {c: [building(i) for i in fetched_ids] for c in grid_tiles},
    ):
        buildings_for_bbox(*bbox)

    cached = local_cache.get(cache_key_for_grid_tile(grid_tile))
    assert [feature["properties"]["id"] for feature in cached] == fetched_ids


def test_buildings_for_bbox_only_fetches_the_grid_tiles_it_is_missing(local_cache):
    bbox = (-0.01, -0.01, 0.01, 0.01)
    grid_tiles = covering_grid_tiles(*bbox)
    cached_grid_tile = grid_tiles[0]
    local_cache.set(cache_key_for_grid_tile(cached_grid_tile), [building("cached")])

    with patch(
        "commcare_connect.microplanning.buildings.fetch_buildings_for_grid_tiles",
        side_effect=lambda grid_tiles: {c: [] for c in grid_tiles},
    ) as fetch:
        buildings_for_bbox(*bbox)

    assert fetch.call_args.args[0] == [grid_tile for grid_tile in grid_tiles if grid_tile != cached_grid_tile]


def _unreadable_reader():
    reader = Mock()
    reader.read_all.side_effect = OSError("connection reset")
    return reader


@pytest.mark.parametrize(
    "patch_kwargs",
    [
        # STAC found no parquet files for the bbox, which the package signals by returning None.
        {"return_value": None},
        # The dataset opened, but reading its rows failed.
        {"return_value": _unreadable_reader()},
        # It never opened: looking up the parquet files and reading their footers happens before
        # any row does, and raises rather than returning None.
        {"side_effect": Exception("Could not open dataset: <S3 error>")},
    ],
    ids=["no reader", "unreadable data", "dataset would not open"],
)
def test_fetch_raises_when_overture_cannot_be_read(patch_kwargs):
    with patch("overturemaps.record_batch_reader", **patch_kwargs):
        with pytest.raises(BuildingDataUnavailable):
            fetch_buildings_for_grid_tiles([(8192, 8191)])


def test_features_by_grid_tile_buckets_each_building_into_every_grid_tile_it_touches():
    boundary_lon = mercantile.bounds(8192, 8191, GRID_ZOOM).west
    grid_tiles = [(8191, 8191), (8192, 8191)]
    straddler = shapely.box(boundary_lon - 0.001, 0.001, boundary_lon + 0.001, 0.002)
    inside = shapely.box(boundary_lon + 0.003, 0.001, boundary_lon + 0.004, 0.002)
    table = _arrow_table([("straddler", straddler), ("inside", inside)])

    by_grid_tile = _features_by_grid_tile(table, grid_tiles)

    assert [f["properties"]["id"] for f in by_grid_tile[(8191, 8191)]] == ["straddler"]
    assert [f["properties"]["id"] for f in by_grid_tile[(8192, 8191)]] == ["straddler", "inside"]
    assert by_grid_tile[(8192, 8191)][1]["geometry"]["type"] == "Polygon"


def test_features_by_grid_tile_returns_an_entry_for_every_requested_grid_tile():
    assert _features_by_grid_tile(_arrow_table([]), [(1, 2), (3, 4)]) == {(1, 2): [], (3, 4): []}
