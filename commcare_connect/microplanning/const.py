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

# Deliver units the coverage metrics are defined on.
SERVICE_DELIVERY_UNIT_SLUG = "services_delivery_unit"
NO_CHILDREN_WORK_AREA_UNIT_SLUG = "no-children-wa"
REQUIRED_DELIVER_UNIT_SLUGS = (SERVICE_DELIVERY_UNIT_SLUG, NO_CHILDREN_WORK_AREA_UNIT_SLUG)

# Zoom levels for the microplanning map. Work area tiles are not served below MIN, so a fit that
# zooms out past it blanks the layer. MAX_AUTOZOOM caps the other end, pinning zoom when the work
# areas are very small e.g. <100m.
# The range of MapBox zoom is 0-22. See https://docs.mapbox.com/help/glossary/zoom-level/.
WORKAREA_MIN_ZOOM = 6
MAX_AUTOZOOM_ZOOM = 18

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

BUILDING_MIN_ZOOM = 15

# Each zoom level divides the map into fixed grid tiles. We can use these grid tiles to cache
# results on so GRID_ZOOM defines the zoom level for which we cache the grid tiles. One grid tile is
# 1024px on screen at BUILDING_MIN_ZOOM, which is what makes MAX_BUILDING_GRID_TILES work out.
GRID_ZOOM = 14

# The most (GRID_ZOOM-size) grid tiles one request's boundary box may cover, so that a single view
# cannot fan out into an unbounded number of upstream fetches.
MAX_BUILDING_GRID_TILES = 16

# The release is part of the key, so a cached grid tile never goes stale under the release it was read
# from and bumping buildings.OVERTURE_RELEASE invalidates every one of them at once.
BUILDINGS_CACHE_KEY = "buildings:{release}:{z}:{x}:{y}"
BUILDINGS_CACHE_TIMEOUT = 60 * 60 * 24 * 7
OVERTURE_BUILDING_TYPE = "building"
OVERTURE_CONNECT_TIMEOUT = 5
OVERTURE_REQUEST_TIMEOUT = 30
