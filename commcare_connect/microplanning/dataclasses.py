from dataclasses import dataclass, field

from django.contrib.gis.geos import Point, Polygon


@dataclass
class WorkAreaRow:
    """One row of a work area CSV. A field that failed to parse is None."""

    line_num: int
    slug: str
    ward: str
    group_name: str
    implementation_area_name: str
    centroid: Point | None
    boundary: Polygon | None
    building_count: int | None
    expected_visit_count: int | None
    target_population: int | None
    lga: str | None
    state: str | None


@dataclass
class ImplementationAreaRow:
    """One row of an implementation area CSV. A field that failed to parse is None."""

    line_num: int
    name: str
    centroid: Point | None
    boundary: Polygon | None


@dataclass
class ParsedAreaCSV:
    """Every row of an area CSV, and the errors found in them as message -> line numbers."""

    rows: list
    errors: dict[str, list[int]] = field(default_factory=dict)
