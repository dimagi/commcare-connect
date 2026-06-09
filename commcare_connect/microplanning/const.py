from commcare_connect.microplanning.models import WorkAreaStatus

WORK_AREA_STATUS_COLORS = {
    WorkAreaStatus.NOT_STARTED: "bg-gray-200 text-gray-700",
    WorkAreaStatus.UNASSIGNED: "bg-gray-200 text-gray-700",
    WorkAreaStatus.NOT_VISITED: "bg-gray-200 text-gray-700",
    WorkAreaStatus.VISITED: "bg-yellow-200 text-yellow-900",
    WorkAreaStatus.REQUEST_FOR_INACCESSIBLE: "bg-yellow-200 text-yellow-900",
    WorkAreaStatus.EXPECTED_VISIT_REACHED: "bg-green-200 text-green-900",
    WorkAreaStatus.INACCESSIBLE: "bg-gray-500 text-white",
    WorkAreaStatus.EXCLUDED: "bg-gray-500 text-white",
}
WORK_AREA_CASE_TYPE = "work-area"
