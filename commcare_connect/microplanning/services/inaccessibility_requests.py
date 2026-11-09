from functools import partial

from django.db import transaction

from commcare_connect.commcarehq.api import create_or_update_case_by_work_area
from commcare_connect.microplanning.const import InaccessibilityReviewAction
from commcare_connect.microplanning.exceptions import InaccessibilityRequestNotPendingError
from commcare_connect.microplanning.models import (
    InaccessibilityRequestStatus,
    WorkArea,
    WorkAreaInaccessibilityRequest,
    WorkAreaStatus,
)

_ACTION_TO_NEW_STATUS = {
    InaccessibilityReviewAction.APPROVE: WorkAreaStatus.INACCESSIBLE,
    InaccessibilityReviewAction.DENY: WorkAreaStatus.NOT_VISITED,
}

_ACTION_TO_REQUEST_STATUS = {
    InaccessibilityReviewAction.APPROVE: InaccessibilityRequestStatus.APPROVED,
    InaccessibilityReviewAction.DENY: InaccessibilityRequestStatus.DENIED,
}


def review_work_area_inaccessibility_request(work_area: WorkArea, action: InaccessibilityReviewAction) -> str:
    """Approve or deny the work area's pending inaccessibility request and sync the area to HQ.

    Returns the work area's new status. Raises InaccessibilityRequestNotPendingError when the area
    has no request awaiting review. HQ errors propagate and roll back the status change. A denial
    notifies the worker after commit.
    """
    inacc_request = (
        WorkAreaInaccessibilityRequest.objects.select_related("opportunity_access__user")
        .filter(work_area=work_area, status=InaccessibilityRequestStatus.PENDING)
        .first()
    )
    if inacc_request is None or work_area.status != WorkAreaStatus.REQUEST_FOR_INACCESSIBLE:
        raise InaccessibilityRequestNotPendingError

    new_status = _ACTION_TO_NEW_STATUS[action]
    work_area.status = new_status
    inacc_request.status = _ACTION_TO_REQUEST_STATUS[action]
    with transaction.atomic():
        work_area.save(update_fields=["status"])
        inacc_request.save(update_fields=["status"])
        if work_area.work_area_group and action == InaccessibilityReviewAction.APPROVE:
            work_area.work_area_group.update_centroid()
        if work_area.opportunity_access_id:
            create_or_update_case_by_work_area(work_area)

    if action == InaccessibilityReviewAction.DENY:
        _notify_worker_of_denial(inacc_request)
    return new_status


def _notify_worker_of_denial(inacc_request: WorkAreaInaccessibilityRequest) -> None:
    from commcare_connect.opportunity.tasks import send_push_notification_task

    transaction.on_commit(
        partial(
            send_push_notification_task.delay,
            [inacc_request.opportunity_access.user_id],
            "Inaccessibility Request Denied",
            "Your request to mark a work area inaccessible has been declined.",
        )
    )
