"""
Reading building footprints from Overture Maps.

This is the only module that knows Overture's storage layout, and it exists so that the rest of
the app is not exposed to it. It replaces the ``overturemaps`` package, and is derived from it:
what follows is that package's ``core.record_batch_reader``, and the STAC lookup behind it,
narrowed to the one query the map makes.

Derived from https://github.com/OvertureMaps/overturemaps-py (MIT licence, Copyright (c) 2024
Overture Maps). It departs from that code deliberately in three places, so a comparison against
upstream is not a list of mistakes: a STAC lookup that fails raises rather than falling back to
scanning the whole release, an area Overture has no buildings for is an empty table rather than
an error, and only the three columns the map draws are read.
"""

import io
from urllib.request import urlopen

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.dataset as ds
import pyarrow.fs as fs
import pyarrow.parquet as pq

from commcare_connect.microplanning.exceptions import BuildingDataUnavailable

STAC_INDEX_URL = "https://stac.overturemaps.org/{release}/collections.parquet"

BUILDING_COLLECTION = "building"

# The bucket is public, and lives in one region.
OVERTURE_S3_REGION = "us-west-2"

BUILDING_SCHEMA = pa.schema(
    [
        ("id", pa.string()),
        ("geometry", pa.binary()),
        ("bbox", pa.struct([(name, pa.float64()) for name in ("xmin", "xmax", "ymin", "ymax")])),
    ]
)
BUILDING_COLUMNS = list(BUILDING_SCHEMA.names)


def read_buildings(bounds, release, connect_timeout, request_timeout):
    """
    Return an arrow table of the buildings Overture has inside ``bounds``, with BUILDING_SCHEMA's
    columns and one row per building.

    An area Overture publishes no buildings for comes back as an empty table; only a read we could
    not make raises ``BuildingDataUnavailable``. The caller can tell the two apart, and so may
    cache the empty answer.
    """
    paths = _intersecting_building_paths(bounds, release, request_timeout)
    if not paths:
        return BUILDING_SCHEMA.empty_table()

    return _read_building_parquet(paths, bounds, _overture_filesystem(connect_timeout, request_timeout))


def _intersecting_building_paths(bounds, release, request_timeout):
    """
    Return the ``bucket/key`` paths of the building parquet files covering ``bounds``.

    An empty list is a real answer rather than a failure: Overture only publishes files where it
    has data, so nothing covering a bbox means there is nothing there to draw.
    """
    index = _read_stac_index(release, request_timeout)
    covering = index.filter((pc.field("collection") == BUILDING_COLLECTION) & _overlapping(bounds))
    # pyarrow reads the bucket and key, so the scheme the index gives them under has to come off.
    return [
        asset["aws"]["alternate"]["s3"]["href"].removeprefix("s3://")
        for asset in covering.column("assets").to_pylist()
    ]


def _read_stac_index(release, request_timeout):
    """Fetch a release's STAC index. pyarrow cannot read HTTP, so it is read into memory first."""
    url = STAC_INDEX_URL.format(release=release)
    try:
        with urlopen(url, timeout=request_timeout) as response:
            return pq.read_table(io.BytesIO(response.read()))
    except Exception as e:
        raise BuildingDataUnavailable(f"Could not read the Overture index at {url}") from e


def _read_building_parquet(paths, bounds, filesystem):
    """Scan the given parquet files, keeping the buildings that overlap ``bounds``."""
    try:
        dataset = ds.dataset(paths, filesystem=filesystem)
        return dataset.to_table(columns=BUILDING_COLUMNS, filter=_overlapping(bounds))
    except Exception as e:
        raise BuildingDataUnavailable(f"Could not read {len(paths)} Overture building file(s)") from e


def _overture_filesystem(connect_timeout, request_timeout):
    """Overture's bucket is public, so it is read anonymously rather than with our credentials."""
    return fs.S3FileSystem(
        anonymous=True,
        region=OVERTURE_S3_REGION,
        connect_timeout=connect_timeout,
        request_timeout=request_timeout,
    )


def _overlapping(bounds):
    """
    A filter keeping rows whose ``bbox`` column overlaps ``bounds``.
    """
    west, south, east, north = bounds
    return (
        (pc.field("bbox", "xmin") < east)
        & (pc.field("bbox", "xmax") > west)
        & (pc.field("bbox", "ymin") < north)
        & (pc.field("bbox", "ymax") > south)
    )
