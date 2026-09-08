import io
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pyarrow as pa
import pyarrow.fs as fs
import pyarrow.parquet as pq
import pytest
import shapely

from commcare_connect.microplanning.exceptions import BuildingDataUnavailable
from commcare_connect.microplanning.overture import _intersecting_building_paths, read_buildings

RELEASE = "2026-08-19.0"
CONNECT_TIMEOUT = 5
REQUEST_TIMEOUT = 30

# The index and the building files both carry a bbox struct, and they order its fields
# differently. The fakes below follow each, so a read that depends on the order fails here.
INDEX_BBOX_TYPE = pa.struct([(name, pa.float64()) for name in ("xmin", "ymin", "xmax", "ymax")])
BUILDING_BBOX_TYPE = pa.struct([(name, pa.float64()) for name in ("xmin", "xmax", "ymin", "ymax")])
ASSETS_TYPE = pa.struct([("aws", pa.struct([("alternate", pa.struct([("s3", pa.struct([("href", pa.string())]))]))]))])


def _stac_index(rows):
    """
    A minimal stand-in for a release's STAC collections.parquet.

    Each row is ``(collection, (xmin, ymin, xmax, ymax), s3_key)``, matching the columns of the
    real index that we read: what the file holds, where it is, and where to fetch it from.
    """
    return pa.table(
        {
            "collection": pa.array([collection for collection, _, _ in rows], pa.string()),
            "bbox": pa.array(
                [dict(zip(("xmin", "ymin", "xmax", "ymax"), bounds)) for _, bounds, _ in rows], INDEX_BBOX_TYPE
            ),
            "assets": pa.array(
                [{"aws": {"alternate": {"s3": {"href": f"s3://{key}"}}}} for _, _, key in rows], ASSETS_TYPE
            ),
        }
    )


@contextmanager
def serving_stac_index(index):
    """Answer the STAC index request with ``index`` instead of going to stac.overturemaps.org."""
    buffer = io.BytesIO()
    pq.write_table(index, buffer)
    response = MagicMock()
    response.__enter__.return_value.read.return_value = buffer.getvalue()
    with patch("commcare_connect.microplanning.overture.urlopen", return_value=response) as urlopen:
        yield urlopen


def test_only_the_building_files_overlapping_the_bbox_are_read():
    index = _stac_index(
        [
            ("building", (-10.0, -10.0, 0.0, 0.0), "bucket/west.parquet"),
            ("building", (0.0, -10.0, 10.0, 0.0), "bucket/east.parquet"),
        ]
    )

    with serving_stac_index(index):
        paths = _intersecting_building_paths((1.0, -1.0, 2.0, -0.5), RELEASE, REQUEST_TIMEOUT)

    assert paths == ["bucket/east.parquet"]


def test_files_for_other_overture_collections_are_ignored():
    index = _stac_index(
        [
            ("segment", (-1.0, -1.0, 1.0, 1.0), "bucket/roads.parquet"),
            ("building", (-1.0, -1.0, 1.0, 1.0), "bucket/buildings.parquet"),
        ]
    )

    with serving_stac_index(index):
        paths = _intersecting_building_paths((0.0, 0.0, 0.5, 0.5), RELEASE, REQUEST_TIMEOUT)

    assert paths == ["bucket/buildings.parquet"]


def test_a_bbox_no_file_covers_yields_no_paths():
    index = _stac_index([("building", (-10.0, -10.0, 0.0, 0.0), "bucket/west.parquet")])

    with serving_stac_index(index):
        assert _intersecting_building_paths((5.0, 5.0, 6.0, 6.0), RELEASE, REQUEST_TIMEOUT) == []


def test_the_index_is_read_for_the_release_we_ask_for():
    with serving_stac_index(_stac_index([])) as urlopen:
        _intersecting_building_paths((0.0, 0.0, 1.0, 1.0), RELEASE, REQUEST_TIMEOUT)

    assert urlopen.call_args.args[0] == f"https://stac.overturemaps.org/{RELEASE}/collections.parquet"


def test_the_index_request_cannot_hang_forever():
    """A bare urlopen has no timeout at all, and this one runs inside a web request."""
    with serving_stac_index(_stac_index([])) as urlopen:
        _intersecting_building_paths((0.0, 0.0, 1.0, 1.0), RELEASE, REQUEST_TIMEOUT)

    assert urlopen.call_args.kwargs["timeout"] == REQUEST_TIMEOUT


def test_a_stac_index_that_cannot_be_fetched_is_reported_as_unavailable():
    with patch("commcare_connect.microplanning.overture.urlopen", side_effect=OSError("connection reset")):
        with pytest.raises(BuildingDataUnavailable):
            _intersecting_building_paths((0.0, 0.0, 1.0, 1.0), RELEASE, REQUEST_TIMEOUT)


def test_a_stac_index_that_is_not_parquet_is_reported_as_unavailable():
    response = MagicMock()
    response.__enter__.return_value.read.return_value = b"<html>502 Bad Gateway</html>"

    with patch("commcare_connect.microplanning.overture.urlopen", return_value=response):
        with pytest.raises(BuildingDataUnavailable):
            _intersecting_building_paths((0.0, 0.0, 1.0, 1.0), RELEASE, REQUEST_TIMEOUT)


def _building_parquet(path, rows, extra_columns=None):
    """Write a parquet file shaped like one of Overture's, plus any columns we do not read."""
    table = pa.table(
        {
            "id": pa.array([feature_id for feature_id, _ in rows], pa.string()),
            "geometry": pa.array([shapely.to_wkb(geometry) for _, geometry in rows], pa.binary()),
            "bbox": pa.array(
                [dict(zip(("xmin", "ymin", "xmax", "ymax"), geometry.bounds)) for _, geometry in rows],
                BUILDING_BBOX_TYPE,
            ),
            **(extra_columns or {}),
        }
    )
    pq.write_table(table, path)
    return path


@contextmanager
def serving_building_files(files):
    """
    Serve real local parquet files as though they were Overture's.

    Each file is ``(path, bounds)``: the bounds go in the index, so which files a read opens is
    decided the same way it is in production, and what the read then does to the rows - the bbox
    pushdown, the column projection - happens for real.
    """
    index = _stac_index([("building", bounds, str(path)) for path, bounds in files])
    with serving_stac_index(index):
        with patch("commcare_connect.microplanning.overture._overture_filesystem", return_value=fs.LocalFileSystem()):
            yield


def test_a_bbox_no_file_covers_yields_no_buildings():
    """Overture publishes files only where it has data, so this is an empty area, not a failure."""
    index = _stac_index([("building", (-10.0, -10.0, 0.0, 0.0), "bucket/west.parquet")])

    with serving_stac_index(index):
        table = read_buildings((5.0, 5.0, 6.0, 6.0), RELEASE, CONNECT_TIMEOUT, REQUEST_TIMEOUT)

    assert table.num_rows == 0
    assert table.column_names == ["id", "geometry", "bbox"]


def test_buildings_outside_the_requested_bbox_are_left_behind(tmp_path):
    path = _building_parquet(
        tmp_path / "buildings.parquet",
        [("inside", shapely.box(0.1, 0.1, 0.2, 0.2)), ("far away", shapely.box(5.0, 5.0, 5.1, 5.1))],
    )

    with serving_building_files([(path, (0.0, 0.0, 10.0, 10.0))]):
        table = read_buildings((0.0, 0.0, 1.0, 1.0), RELEASE, CONNECT_TIMEOUT, REQUEST_TIMEOUT)

    assert table.column("id").to_pylist() == ["inside"]


def test_only_the_columns_the_map_needs_are_read(tmp_path):
    """Overture publishes about thirty columns per building; parquet lets us pay for three."""
    path = _building_parquet(
        tmp_path / "buildings.parquet",
        [("a building", shapely.box(0.1, 0.1, 0.2, 0.2))],
        extra_columns={"names": pa.array(["Clinic"], pa.string()), "height": pa.array([12.0], pa.float64())},
    )

    with serving_building_files([(path, (0.0, 0.0, 10.0, 10.0))]):
        table = read_buildings((0.0, 0.0, 1.0, 1.0), RELEASE, CONNECT_TIMEOUT, REQUEST_TIMEOUT)

    assert table.column_names == ["id", "geometry", "bbox"]


def test_every_file_covering_the_bbox_is_read(tmp_path):
    """A bbox on a partition boundary has its buildings split across files."""
    west = _building_parquet(tmp_path / "west.parquet", [("west", shapely.box(-0.2, 0.1, -0.1, 0.2))])
    east = _building_parquet(tmp_path / "east.parquet", [("east", shapely.box(0.1, 0.1, 0.2, 0.2))])

    with serving_building_files([(west, (-10.0, -10.0, 0.0, 10.0)), (east, (0.0, -10.0, 10.0, 10.0))]):
        table = read_buildings((-1.0, 0.0, 1.0, 1.0), RELEASE, CONNECT_TIMEOUT, REQUEST_TIMEOUT)

    assert sorted(table.column("id").to_pylist()) == ["east", "west"]


def test_parquet_that_cannot_be_read_is_reported_as_unavailable(tmp_path):
    path = tmp_path / "buildings.parquet"
    path.write_bytes(b"<html>503 Slow Down</html>")

    with serving_building_files([(path, (0.0, 0.0, 10.0, 10.0))]):
        with pytest.raises(BuildingDataUnavailable):
            read_buildings((0.0, 0.0, 1.0, 1.0), RELEASE, CONNECT_TIMEOUT, REQUEST_TIMEOUT)


def test_an_empty_read_and_a_full_one_come_back_the_same_shape(tmp_path):
    """The two are built by different code paths, so a caller may combine them."""
    path = _building_parquet(tmp_path / "buildings.parquet", [("a building", shapely.box(0.1, 0.1, 0.2, 0.2))])

    with serving_building_files([(path, (0.0, 0.0, 10.0, 10.0))]):
        buildings = read_buildings((0.0, 0.0, 1.0, 1.0), RELEASE, CONNECT_TIMEOUT, REQUEST_TIMEOUT)
        nothing = read_buildings((50.0, 50.0, 51.0, 51.0), RELEASE, CONNECT_TIMEOUT, REQUEST_TIMEOUT)

    assert pa.concat_tables([buildings, nothing]).num_rows == 1
