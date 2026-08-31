from django.utils.translation import gettext_lazy as _

from commcare_connect.microplanning.models import WorkAreaStatus

WORK_AREA_STATUS_COLORS = {
    WorkAreaStatus.UNASSIGNED: "bg-gray-200 text-gray-700",
    WorkAreaStatus.NOT_VISITED: "bg-gray-200 text-gray-700",
    WorkAreaStatus.VISITED: "bg-yellow-200 text-yellow-900",
    WorkAreaStatus.REQUEST_FOR_INACCESSIBLE: "bg-yellow-200 text-yellow-900",
    WorkAreaStatus.EXPECTED_VISIT_REACHED: "bg-green-200 text-green-900",
    WorkAreaStatus.INACCESSIBLE: "bg-gray-500 text-white",
    WorkAreaStatus.EXCLUDED: "bg-gray-500 text-white",
}
WORK_AREA_CASE_TYPE = "work-area"

# Fill colours for the Inaccessibility Review map and its key. Keyed rather than positional so the
# map paint expression and the key's swatches cannot drift apart.
INACCESSIBILITY_LEGEND = [
    {"key": "requested", "color": "#f59e0b", "label": _("Request for Inaccessible")},
    {"key": "approved", "color": "#bbf7d0", "label": _("Approved as Inaccessible")},
    {"key": "denied", "color": "#fecaca", "label": _("Denied")},
    {"key": "other", "color": "#e5e7eb", "label": _("All other work areas")},
]

# Deliver units the coverage metrics are defined on.
SERVICE_DELIVERY_UNIT_SLUG = "services_delivery_unit"
NO_CHILDREN_WORK_AREA_UNIT_SLUG = "no-children-wa"
REQUIRED_DELIVER_UNIT_SLUGS = (SERVICE_DELIVERY_UNIT_SLUG, NO_CHILDREN_WORK_AREA_UNIT_SLUG)

# Zoom levels for the microplanning map. Work area tiles are not served below MIN, so a fit that
# zooms out past it blanks the layer; MAX_AUTOZOOM keeps a fit onto a single work area (a few
# hundred metres across) from landing at street level.
WORKAREA_MIN_ZOOM = 6
MAX_AUTOZOOM_ZOOM = 14

MAX_EXCLUDE_WORK_AREAS = 200
MAX_UNASSIGN_WORK_AREAS = 200
HQ_BULK_CHUNK_SIZE = 50
HQ_UNASSIGN_BULK_CHUNK_SIZE = 200
HQ_ASSIGN_BULK_CHUNK_SIZE = 100

# Bounds for the user-configurable target building count per work area group.
MIN_BUILDING_COUNT = 100
MAX_BUILDING_COUNT = 300
DEFAULT_BUILDING_COUNT = 200

SEARCH_KIND_FILTERS = {
    "wa": "work_area",
    "wag": "work_area_group",
    "ia": "implementation_area",
}
