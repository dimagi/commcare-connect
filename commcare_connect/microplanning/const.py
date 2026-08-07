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
