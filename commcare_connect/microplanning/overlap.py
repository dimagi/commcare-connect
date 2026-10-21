"""
Overlap check for work areas.

Two work areas overlap when the area they share is more than WORK_AREA_MAX_OVERLAP_RATIO of the
smaller one's area. Measuring against the smaller one catches a small work area drawn inside a large
one (100%), and the tolerance lets through the slivers that boundaries from GIS tools often share.
PostGIS's ST_Overlaps can't be used instead: it is false when one shape contains the other, and it
has no tolerance. Work areas that only share an edge or a corner share no area, so they pass.

Excluded work areas are left out, so a replacement can be drawn over the same ground. Inaccessible
ones are checked, so ground that was ruled out isn't added again.
"""

from typing import TypedDict

from django.contrib.gis.db.models.functions import Area, Intersection, MakeValid
from django.db import connection
from django.db.models import F, FilteredRelation, FloatField, Q, Value
from django.db.models.functions import Cast, Least, NullIf

from .const import WORK_AREA_MAX_OVERLAP_RATIO
from .models import WorkArea, WorkAreaStatus


class Overlap(TypedDict):
    """One overlapping pair, as a row of ``find_overlapping_work_areas``."""

    pk: int  # the new work area
    other_id: int
    other_slug: str
    ratio: float  # shared area / the smaller work area's area


def find_overlapping_work_areas(new_ids: list[int]):
    """Pairs of a new work area and another non-excluded work area in its opportunity that overlap.

    Each row is an ``Overlap``. A pair of two new work areas is returned once, with the lower ``pk``
    first.

    The new work areas' boundaries must be valid; the importer rejects invalid ones. Other boundaries
    are repaired before measuring, though matching them via the spatial index uses the saved shape.
    """
    # Count the new rows in the opportunity's statistics, or Postgres may skip the spatial index.
    table = WorkArea._meta.db_table
    column = WorkArea._meta.get_field("opportunity").column
    with connection.cursor() as cursor:
        cursor.execute(f"ANALYZE {connection.ops.quote_name(table)} ({connection.ops.quote_name(column)})")
    return (
        WorkArea.objects.filter(pk__in=new_ids)
        .annotate(
            other=FilteredRelation(
                "opportunity__workarea",
                condition=Q(opportunity__workarea__boundary__intersects=F("boundary"))
                & ~Q(opportunity__workarea__pk=F("pk"))
                & ~Q(opportunity__workarea__status=WorkAreaStatus.EXCLUDED)
                & ~Q(opportunity__workarea__pk__in=new_ids, opportunity__workarea__pk__lt=F("pk")),
            )
        )
        .filter(other__isnull=False)
        .annotate(
            other_id=F("other__pk"),
            other_slug=F("other__slug"),
            other_boundary=MakeValid("other__boundary"),
        )
        .annotate(
            ratio=_area(Intersection("boundary", "other_boundary"))
            / NullIf(Least(_area("boundary"), _area("other_boundary")), Value(0.0)),
        )
        .filter(ratio__gt=WORK_AREA_MAX_OVERLAP_RATIO)
        .order_by("pk", "other_id")
        .values("pk", "other_id", "other_slug", "ratio")
    )


def _area(expression):
    return Cast(Area(expression), FloatField())
