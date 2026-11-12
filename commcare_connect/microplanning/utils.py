from django.contrib.gis.db.models import Extent

from commcare_connect.microplanning.const import REQUIRED_DELIVER_UNIT_SLUGS
from commcare_connect.microplanning.coverage_progress import missing_deliver_units


def work_area_bounds(queryset):
    """``[xmin, ymin, xmax, ymax]`` covering ``queryset``, or None when it is empty.

    Mapbox's ``fitBounds`` takes this shape directly, so it doubles as the "where should the map
    look?" answer — no separate centroid needed.
    """
    return queryset.aggregate(extent=Extent("boundary"))["extent"]


def quoted_missing_deliver_units(opportunity):
    missing_units = missing_deliver_units(opportunity, list(REQUIRED_DELIVER_UNIT_SLUGS))
    return ", ".join([f'"{unit}"' for unit in missing_units])
