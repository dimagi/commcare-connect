import csv
from collections import defaultdict
from collections.abc import Iterator
from typing import TextIO

from django.contrib.gis.geos import GEOSException, GEOSGeometry, Point, Polygon
from django.utils.html import strip_tags
from django.utils.translation import gettext as _

from commcare_connect.microplanning.const import (
    IMPLEMENTATION_AREA_CSV_HEADERS,
    WORK_AREA_CSV_GROUP_NAME_HEADER,
    WORK_AREA_CSV_HEADERS,
    WORK_AREA_CSV_IMPLEMENTATION_AREA_HEADER,
)
from commcare_connect.microplanning.dataclasses import ImplementationAreaRow, ParsedAreaCSV, WorkAreaRow
from commcare_connect.microplanning.exceptions import AreaCSVColumnsError
from commcare_connect.microplanning.models import SRID


def parse_lon_lat_centroid(value: str) -> Point:
    """Parse a "lon lat" string into a centroid Point."""
    lon, lat = value.strip().split()
    return GEOSGeometry(f"POINT({lon} {lat})", srid=SRID)


def parse_work_area_csv(csv_source: TextIO) -> ParsedAreaCSV:
    """Read a work area CSV into rows and check each row's values.

    Header names match case-insensitively. Raises AreaCSVColumnsError when a required column is
    missing. Errors in the rows are returned with them, so they can be reported together with
    errors found against the database.
    """
    errors = defaultdict(list)
    rows = []
    seen_slugs = set()
    headers = WORK_AREA_CSV_HEADERS
    for line_num, row in _read_rows(
        csv_source,
        required_headers=list(headers.values()),
        canonical_headers=[*headers.values(), WORK_AREA_CSV_GROUP_NAME_HEADER],
    ):
        slug = _clean(row.get(headers["slug"]))
        if not slug:
            errors[_("Area slug is required and it should be unique.")].append(line_num)
        elif slug in seen_slugs:
            errors[_("Duplicate Area slug in file")].append(line_num)
        else:
            seen_slugs.add(slug)

        ward = _clean(row.get(headers["ward"]))
        if not ward:
            errors[_("Ward is required.")].append(line_num)

        centroid = _parse_centroid(row.get(headers["centroid"]), line_num=line_num, errors=errors)

        boundary = _parse_boundary(row, header=headers["boundary"], line_num=line_num, errors=errors)
        # A ring that crosses itself still parses as a Polygon, but PostGIS can't measure it: its
        # area reads as 0 and intersecting it raises, which would abort the import.
        if boundary is not None and not boundary.valid:
            errors[
                _("Boundary is not a valid polygon, e.g. its edges cross. Fix the boundary, then upload again.")
            ].append(line_num)
            boundary = None

        try:
            building_count = _parse_int(row.get(headers["building_count"]))
            expected_visit_count = _parse_int(row.get(headers["visit_count"]))
            target_population = _parse_int(row.get(headers["target_population"]))
            numbers_valid = building_count >= 0 and expected_visit_count >= 0 and target_population >= 0
        except ValueError:
            numbers_valid = False
        if not numbers_valid:
            building_count = expected_visit_count = target_population = None
            errors[_("Building count, Expected visit count, and Target population must be positive integers")].append(
                line_num
            )

        extra_properties = {"lga": row.get(headers["lga"]), "state": row.get(headers["state"])}
        missing_values = [key for key, value in extra_properties.items() if not value]
        if missing_values:
            errors[_("Missing values for properties: ") + ", ".join(missing_values)].append(line_num)

        rows.append(
            WorkAreaRow(
                line_num=line_num,
                slug=slug,
                ward=ward,
                group_name=_clean(row.get(WORK_AREA_CSV_GROUP_NAME_HEADER)),
                implementation_area_name=(row.get(WORK_AREA_CSV_IMPLEMENTATION_AREA_HEADER) or "").strip(),
                centroid=centroid,
                boundary=boundary,
                building_count=building_count,
                expected_visit_count=expected_visit_count,
                target_population=target_population,
                lga=extra_properties["lga"],
                state=extra_properties["state"],
            )
        )

    rows_with_group = sum(1 for row in rows if row.group_name)
    if 0 < rows_with_group < len(rows):
        errors[_("Work Area Group Name is required for all Work Areas, or must be omitted for all.")].append(1)
    return ParsedAreaCSV(rows=rows, errors=errors)


def parse_implementation_area_csv(csv_source: TextIO) -> ParsedAreaCSV:
    """Read an implementation area CSV into rows and check each row's values.

    Header names match case-insensitively. Raises AreaCSVColumnsError when a required column is
    missing.
    """
    errors = defaultdict(list)
    rows = []
    seen_names = set()
    headers = IMPLEMENTATION_AREA_CSV_HEADERS
    for line_num, row in _read_rows(
        csv_source,
        required_headers=list(headers.values()),
        canonical_headers=list(headers.values()),
    ):
        name = _clean(row.get(headers["name"]))
        if not name:
            errors[_("Implementation Area Name is required and should be unique.")].append(line_num)
        elif name in seen_names:
            errors[_("Duplicate Implementation Area Name in file")].append(line_num)
        else:
            seen_names.add(name)

        rows.append(
            ImplementationAreaRow(
                line_num=line_num,
                name=name,
                centroid=_parse_centroid(row.get(headers["centroid"]), line_num=line_num, errors=errors),
                boundary=_parse_boundary(row, header=headers["boundary"], line_num=line_num, errors=errors),
            )
        )
    return ParsedAreaCSV(rows=rows, errors=errors)


def _read_rows(
    csv_source: TextIO, required_headers: list[str], canonical_headers: list[str]
) -> Iterator[tuple[int, dict]]:
    """Yield (line number, row) for each row, with headers spelled as in canonical_headers."""
    csv_source.seek(0)
    reader = csv.DictReader(csv_source)
    canonical_by_lower = {header.lower(): header for header in canonical_headers}
    reader.fieldnames = [canonical_by_lower.get((h or "").lower(), h) for h in (reader.fieldnames or [])]

    missing = set(required_headers) - set(reader.fieldnames)
    if missing:
        raise AreaCSVColumnsError(_("Missing columns: ") + ", ".join(sorted(missing)))
    yield from enumerate(reader, start=2)


def _clean(value: str | None) -> str:
    return strip_tags((value or "").strip())


def _parse_int(value: str | None) -> int:
    return int(value) if value else 0


def _parse_centroid(value: str | None, line_num: int, errors: dict[str, list[int]]) -> Point | None:
    try:
        return parse_lon_lat_centroid(value)
    except (ValueError, GEOSException, TypeError, AttributeError):
        errors[_("Centroid must be in 'lon lat' format")].append(line_num)
        return None


def _parse_boundary(row: dict, header: str, line_num: int, errors: dict[str, list[int]]) -> Polygon | None:
    try:
        geom = GEOSGeometry(row.get(header, "").strip(), srid=SRID)
        if geom.geom_type == "Polygon":
            return geom
    except (GEOSException, ValueError, TypeError, AttributeError):
        pass
    errors[_("Invalid WKT format for Boundary(Polygon).")].append(line_num)
    return None
